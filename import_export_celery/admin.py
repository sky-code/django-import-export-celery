# Copyright (C) 2019 o.s. Auto*Mat
import json
import pickle
from base64 import b64encode

import django
from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.cache import cache
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from import_export.admin import ExportMixin
from import_export.forms import SelectableFieldsExportForm

from . import admin_actions, models
from .models import ExportJob


class JobWithStatusMixin:
    @admin.display(description=_("Job status info"))
    def job_status_info(self, obj):
        job_status = cache.get(self.direction + "_job_status_%s" % obj.pk)
        if job_status:
            return job_status
        else:
            return obj.job_status


class ImportJobForm(forms.ModelForm):
    model = forms.ChoiceField(label=_("Name of model to import to"))

    class Meta:
        model = models.ImportJob
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model"].choices = [
            (x, x) for x in getattr(settings, "IMPORT_EXPORT_CELERY_MODELS", {}).keys()
        ]
        self.fields["format"].widget = forms.Select(
            choices=self.instance.get_format_choices()
        )


@admin.register(models.ImportJob)
class ImportJobAdmin(JobWithStatusMixin, admin.ModelAdmin):
    direction = "import"
    form = ImportJobForm
    list_display = (
        "model",
        "job_status_info",
        "file",
        "change_summary",
        "imported",
        "author",
        "updated_by",
    )
    readonly_fields = (
        "job_status_info",
        "change_summary",
        "imported",
        "errors",
        "author",
        "updated_by",
        "processing_initiated",
    )
    exclude = ("job_status",)

    list_filter = ("model", "imported")

    actions = (
        admin_actions.run_import_job_action,
        admin_actions.run_import_job_action_dry,
    )


class ExportJobForm(forms.ModelForm):
    class Meta:
        model = models.ExportJob
        exclude = ("site_of_origin",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resource"].widget = forms.Select(
            choices=self.instance.get_resource_choices()
        )
        self.fields["format"].widget = forms.Select(
            choices=self.instance.get_format_choices()
        )


@admin.register(models.ExportJob)
class ExportJobAdmin(JobWithStatusMixin, admin.ModelAdmin):
    direction = "export"
    form = ExportJobForm
    list_display = (
        "model",
        "app_label",
        "file",
        "job_status_info",
        "author",
        "updated_by",
    )
    readonly_fields = (
        "job_status_info",
        "author",
        "updated_by",
        "app_label",
        "model",
        "file",
        "processing_initiated",
    )
    exclude = ("job_status",)

    list_filter = ("model",)

    def has_add_permission(self, request, obj=None):
        return False

    actions = (admin_actions.run_export_job_action,)


class BackgroundJobSelectableFieldsExportForm(SelectableFieldsExportForm):
    background_job = forms.BooleanField(
        required=False,
        initial=False,
        label="Run export in background, will export all fields",
    )


class BackgroundJobExportMixin(ExportMixin):
    export_form_class = BackgroundJobSelectableFieldsExportForm

    def _do_file_export(self, file_format, request, queryset, export_form=None):
        if not export_form.cleaned_data.get("background_job"):
            return super()._do_file_export(file_format, request, queryset, export_form=export_form)
        base64_pickle_qs = b64encode(pickle.dumps(queryset.query)).decode("ascii")
        export_class = self.choose_export_resource_class(export_form, request)
        model_class = export_class.Meta.model
        resource_classes = model_class.export_resource_classes()
        resource = None
        for resource_key, resource_choice in resource_classes.items():
            _resource_label, resource_class = resource_choice
            if resource_class == export_class:
                resource = resource_key
                break
        job = ExportJob(
            app_label=self.model._meta.app_label,
            model=self.model._meta.model_name,
            site_of_origin=request.scheme + "://" + request.get_host(),
            queryset=json.dumps(
                {
                    "queryString": request.GET.urlencode(),
                    "djangoVersion": django.get_version(),
                    "query": base64_pickle_qs,
                }
            ),
            resource=resource,
            format=file_format.CONTENT_TYPE,
        )
        job.save()
        return redirect(f"admin:{job._meta.app_label}_{job._meta.model_name}_change", job.id)
