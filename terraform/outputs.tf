output "central_service_account_member" {
  description = "IAM member string granted access to the target projects."
  value       = local.central_service_account_member
}

output "target_project_ids" {
  description = "Projects managed by this Terraform state."
  value       = sort(tolist(local.target_project_ids))
}

output "target_billing_account_ids" {
  description = "Cloud Billing accounts listed in projects, whether or not Billing IAM management is enabled."
  value       = sort(tolist(local.target_billing_account_ids))
}

output "granted_roles_by_project" {
  description = "Project-level roles managed by this Terraform state."
  value = {
    for project_id in local.target_project_ids : project_id => sort(concat(
      tolist(local.project_roles),
      ["roles/iam.serviceAccountUser"],
    ))
  }
}

output "granted_billing_roles_by_account" {
  description = "Billing-account-level roles managed by this Terraform state."
  value = {
    for billing_account_id in local.billing_iam_account_ids :
    billing_account_id => ["roles/billing.viewer"]
  }
}

output "enabled_services_by_project" {
  description = "Google APIs enabled and retained by this Terraform state."
  value = {
    for project_id in local.target_project_ids : project_id => [
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
    for project_id in local.target_project_ids : project_id => {
      enable-osconfig = "TRUE"
    }
  }
}

output "problem_vms" {
  description = "Created problem VMs. Broker inventory sync and Bootstrap installation are separate steps."
  value = {
    for key, instance in google_compute_instance.problems : key => {
      project_id            = instance.project
      name                  = instance.name
      instance_id           = instance.instance_id
      zone                  = instance.zone
      machine_type          = instance.machine_type
      current_status        = instance.current_status
      boot_disk_size_gb     = instance.boot_disk[0].initialize_params[0].size
      service_account_email = instance.service_account[0].email
      private_ip            = instance.network_interface[0].network_ip
      public_ip             = instance.network_interface[0].access_config[0].nat_ip
    }
  }
}
