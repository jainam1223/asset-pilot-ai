variable "resource_group_name" {
  type        = string
  description = "Name of the existing resource group"
  default     = "prodessy"
}

variable "app_service_plan_name" {
  type        = string
  description = "Name of the existing App Service Plan"
  default     = "asp-asset-pilot"
}

variable "acr_name" {
  type        = string
  description = "Name of the existing Azure Container Registry"
  default     = "assetpilotacr2026"
}

variable "app_name" {
  type        = string
  description = "Name for the new Azure App Service"
  default     = "app-asset-pilot-ai"
}

variable "docker_image_tag" {
  type        = string
  description = "The tag for the docker image to deploy (usually github.sha)"
  default     = "latest"
}
