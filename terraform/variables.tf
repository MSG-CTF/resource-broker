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

variable "manage_billing_iam" {
  description = "Manage Billing Account Viewer grants for the central Broker service account. Leave false while cost tracking is deferred."
  type        = bool
  default     = false
  nullable    = false
}

variable "problem_vms_running" {
  description = "Keep problem VMs running when true; stop them without deleting disks or static IPs when false."
  type        = bool
  default     = true
  nullable    = false
}

variable "projects" {
  description = "GCP projects keyed by project ID, with their existing Billing account and VM region/zone."
  type = map(object({
    billing_account_id = string
    region             = string
    zone               = string
  }))
  nullable = false

  validation {
    condition = (
      length(var.projects) > 0 &&
      alltrue([
        for project_id in keys(var.projects) :
        can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", project_id))
      ])
    )
    error_message = "projects must contain at least one entry keyed by a valid GCP project ID."
  }

  validation {
    condition = alltrue([
      for project in values(var.projects) :
      can(regex("^[0-9A-Z]{6}-[0-9A-Z]{6}-[0-9A-Z]{6}$", project.billing_account_id))
    ])
    error_message = "Each project must have a valid billing_account_id in XXXXXX-XXXXXX-XXXXXX format."
  }

  validation {
    condition = alltrue([
      for project in values(var.projects) :
      can(regex("^[a-z]+-[a-z]+[0-9]+$", project.region)) &&
      can(regex("^${project.region}-[a-z]$", project.zone))
    ])
    error_message = "Each project must have a valid region and a zone in that region, such as us-central1 and us-central1-a."
  }
}
