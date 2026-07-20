import { getModelConfigurationPricingApiV1OrganizationsModelConfigurationsV2PricingGet } from "@/client/sdk.gen";
import type { ModelConfigurationPricingResponse } from "@/client/types.gen";
import logger from "@/lib/logger";

export async function fetchModelConfigurationPricing(): Promise<ModelConfigurationPricingResponse | null> {
    try {
        const result = await getModelConfigurationPricingApiV1OrganizationsModelConfigurationsV2PricingGet();
        if (result.error) {
            logger.warn("Failed to load model configuration pricing", result.error);
            return null;
        }
        return result.data ?? null;
    } catch (error) {
        logger.warn("Failed to load model configuration pricing", error);
        return null;
    }
}
