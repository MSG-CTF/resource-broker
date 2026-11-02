variable "central_service_account_email" {
  description = "Existing Broker service account for KOTH inventory."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.iam\\.gserviceaccount\\.com$", var.central_service_account_email))
    error_message = "Set a valid Google service account email."
  }
}

variable "project_ids" {
  description = "Existing, billing-enabled GCP projects: shared hosts Card/Jenga/Swanopoly; heavy hosts Dependency Hell's two nodes; pixel_grand hosts Pixel Smuggler and Grand Tour Duel."
  type = object({
    shared      = string
    heavy       = string
    pixel_grand = string
  })
  nullable = false

  validation {
    condition = (
      length(distinct(values(var.project_ids))) == length(values(var.project_ids)) &&
      alltrue([
        for project_id in values(var.project_ids) :
        can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", project_id))
      ])
    )
    error_message = "Set shared, heavy, and pixel_grand to three different, valid GCP project IDs."
  }
}

variable "enabled_project_groups" {
  description = "Project groups to deploy. Keep already deployed groups enabled to retain their resources."
  type        = set(string)
  default     = ["shared", "heavy", "pixel_grand"]
  nullable    = false

  validation {
    condition     = length(var.enabled_project_groups) > 0 && length(setsubtract(var.enabled_project_groups, ["shared", "heavy", "pixel_grand"])) == 0
    error_message = "Select shared, heavy, pixel_grand, or a combination of these groups."
  }
}

variable "problem_vms_running" {
  description = "Set false to stop enabled VMs while retaining disks and static IPv4 addresses, which remain billable."
  type        = bool
  default     = true
  nullable    = false
}

variable "enabled_shared_challenges" {
  description = "Shared-project challenges to deploy. Pixel Smuggler belongs to pixel_grand."
  type        = set(string)
  default     = ["card-battle-arena", "jenga", "swanopoly"]
  nullable    = false

  validation {
    condition = (
      length(var.enabled_shared_challenges) > 0 &&
      length(setsubtract(var.enabled_shared_challenges, ["card-battle-arena", "jenga", "swanopoly"])) == 0
    )
    error_message = "Select one or more valid shared-project challenge names."
  }
}
