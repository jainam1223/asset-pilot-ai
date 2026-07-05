terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
  backend "azurerm" {
    resource_group_name  = "prodessy"
    storage_account_name = "tfstateprod178967"
    container_name       = "tfstate"
    # Using asset-pilot-ai instead of node to prevent overwriting the other app's state
    key                  = "asset-pilot-ai.tfstate" 
  }
}

provider "azurerm" {
  features {}
}
