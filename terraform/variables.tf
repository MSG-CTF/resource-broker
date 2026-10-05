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

variable "target_billing_account_ids" {
  description = "Unique Cloud Billing account IDs whose cost information the central service account can view."
  type        = set(string)

  validation {
    condition = (
      length(var.target_billing_account_ids) > 0 &&
      alltrue([
        for billing_account_id in var.target_billing_account_ids :
        can(regex("^[0-9A-Z]{6}-[0-9A-Z]{6}-[0-9A-Z]{6}$", billing_account_id))
      ])
    )
    error_message = "target_billing_account_ids must contain at least one valid Cloud Billing account ID in XXXXXX-XXXXXX-XXXXXX format."
  }
}
