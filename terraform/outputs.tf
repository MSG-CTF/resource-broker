output "central_service_account_member" {
  description = "IAM member string granted access to the target projects."
  value       = local.central_service_account_member
}

output "target_project_ids" {
  description = "Projects managed by this Terraform state."
  value       = sort(tolist(var.target_project_ids))
}

output "target_billing_account_ids" {
  description = "Cloud Billing accounts whose cost information is visible to the central service account."
  value       = sort(tolist(var.target_billing_account_ids))
}

output "granted_roles_by_project" {
  description = "Project-level roles managed by this Terraform state."
  value = {
    for project_id in var.target_project_ids : project_id => sort(concat(
      tolist(local.project_roles),
      ["roles/iam.serviceAccountUser"],
    ))
  }
}

output "granted_billing_roles_by_account" {
  description = "Billing-account-level roles managed by this Terraform state."
  value = {
    for billing_account_id in var.target_billing_account_ids :
    billing_account_id => ["roles/billing.viewer"]
  }
}

output "enabled_services_by_project" {
  description = "Google APIs enabled and retained by this Terraform state."
  value = {
    for project_id in var.target_project_ids : project_id => [
      "bigquery.googleapis.com",
      "cloudbilling.googleapis.com",
      "compute.googleapis.com",
      "osconfig.googleapis.com",
    ]
  }
}

output "osconfig_metadata_by_project" {
  description = "Project metadata used to activate the OS Config agent."
  value = {
    for project_id in var.target_project_ids : project_id => {
      enable-osconfig = "TRUE"
    }
  }
}
