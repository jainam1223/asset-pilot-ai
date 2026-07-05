data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

data "azurerm_service_plan" "asp" {
  name                = var.app_service_plan_name
  resource_group_name = data.azurerm_resource_group.rg.name
}

data "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = data.azurerm_resource_group.rg.name
}

resource "azurerm_linux_web_app" "app" {
  name                = var.app_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  service_plan_id     = data.azurerm_service_plan.asp.id

  site_config {
    application_stack {
      docker_image_name   = "asset-pilot-ai:${var.docker_image_tag}"
      docker_registry_url = "https://${data.azurerm_container_registry.acr.login_server}"
    }
  }

  lifecycle {
    ignore_changes = [
      site_config[0].application_stack[0].docker_image_name
    ]
  }

  app_settings = {
    # Provide the App Service with credentials to pull the image from ACR
    "DOCKER_REGISTRY_SERVER_URL"          = "https://${data.azurerm_container_registry.acr.login_server}"
    "DOCKER_REGISTRY_SERVER_USERNAME"     = data.azurerm_container_registry.acr.admin_username
    "DOCKER_REGISTRY_SERVER_PASSWORD"     = data.azurerm_container_registry.acr.admin_password
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"
  }
}
