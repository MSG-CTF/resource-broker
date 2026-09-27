variable "central_service_account_email" {
  description = "Email address of the existing central MSG Broker service account."
  type        = string

  validation {
    condition = (
      trimspace(var.central_service_account_email) == var.central_service_account_email &&
      can(regex("^[^@\\s]+@[^@\\s]+\\.iam\\.gserviceaccount\\.com$", var.central_service_account_email))
    )
    error_message = "central_service_account_email must be a valid Google service account email without surrounding whitespace."
  }
}

variable "target_project_ids" {
  description = "Unique GCP project IDs in which the central service account will manage Compute Engine resources."
  type        = set(string)

  validation {
    condition = (
      length(var.target_project_ids) > 0 &&
      alltrue([
        for project_id in var.target_project_ids :
        can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", project_id))
      ])
    )
    error_message = "target_project_ids must contain at least one valid GCP project ID."
  }
}

variable "enable_vm_manager_bootstrap" {
  description = "Grant OS Policy Assignment write and report read permissions used by the Broker GCP Bootstrap flow. This is equivalent to remote code execution on matching VMs."
  type        = bool
  default     = false
}

variable "grant_project_wide_service_account_user" {
  description = "Grant roles/iam.serviceAccountUser at each target project. Enable only when the Broker must attach arbitrary project service accounts to VMs."
  type        = bool
  default     = false
}
