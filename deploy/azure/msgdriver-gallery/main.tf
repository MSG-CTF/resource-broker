terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.50.0"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id                 = "6c549f44-9b55-4bdc-811e-75cca1abe0e4"
  tenant_id                       = "946167a9-0c6b-455b-91ac-f855158ad79a"
  resource_provider_registrations = "none"
}

resource "azurerm_shared_image_gallery" "msgdriver" {
  name                = "msgdrivergallery"
  resource_group_name = "msg"
  location            = "koreacentral"
  description         = "MSG Driver Windows recovery image"
}

resource "azurerm_shared_image" "msgdriver" {
  name                   = "msgdriver-v5"
  gallery_name           = azurerm_shared_image_gallery.msgdriver.name
  resource_group_name    = azurerm_shared_image_gallery.msgdriver.resource_group_name
  location               = azurerm_shared_image_gallery.msgdriver.location
  os_type                = "Windows"
  specialized            = true
  hyper_v_generation     = "V2"
  trusted_launch_enabled = true

  identifier {
    publisher = "MSG"
    offer     = "msgdriver"
    sku       = "windows-v5"
  }
}

resource "azurerm_shared_image_version" "v5" {
  name                = "1.0.0"
  gallery_name        = azurerm_shared_image_gallery.msgdriver.name
  image_name          = azurerm_shared_image.msgdriver.name
  resource_group_name = azurerm_shared_image_gallery.msgdriver.resource_group_name
  location            = azurerm_shared_image_gallery.msgdriver.location
  os_disk_snapshot_id = "/subscriptions/6c549f44-9b55-4bdc-811e-75cca1abe0e4/resourceGroups/msg/providers/Microsoft.Compute/snapshots/msgdriver-clean-v5"

  target_region {
    name                   = "koreacentral"
    regional_replica_count = 1
    storage_account_type   = "Standard_LRS"
  }
}

output "image_version_id" {
  value = azurerm_shared_image_version.v5.id
}
