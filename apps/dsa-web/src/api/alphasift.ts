import apiClient from './index';
import { systemConfigApi } from './systemConfig';
import { toCamelCase } from './utils';

export type AlphaSiftStatus = {
  enabled: boolean;
  available: boolean;
  installSpec: string;
};

export type AlphaSiftInstallResponse = {
  installed: boolean;
  alreadyInstalled: boolean;
  installSpec: string;
};

export type AlphaSiftCandidate = {
  rank: number;
  code: string;
  name: string;
  score?: number | null;
  reason: string;
  raw: Record<string, unknown>;
};

export type AlphaSiftScreenResponse = {
  enabled: boolean;
  candidates: AlphaSiftCandidate[];
  candidateCount: number;
};

export function notifyAlphaSiftConfigChanged(): void {
  window.dispatchEvent(new Event('alphasift-config-changed'));
}

export const alphasiftApi = {
  async getStatus(): Promise<AlphaSiftStatus> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/alphasift/status');
    return toCamelCase<AlphaSiftStatus>(response.data);
  },

  async screen(payload: { market: string; strategy: string; maxResults: number }): Promise<AlphaSiftScreenResponse> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/alphasift/screen', {
      market: payload.market,
      strategy: payload.strategy,
      max_results: payload.maxResults,
    });
    return toCamelCase<AlphaSiftScreenResponse>(response.data);
  },

  async install(): Promise<AlphaSiftInstallResponse> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/alphasift/install', {}, { timeout: 180000 });
    return toCamelCase<AlphaSiftInstallResponse>(response.data);
  },

  async enable(): Promise<void> {
    const config = await systemConfigApi.getConfig(false);
    await systemConfigApi.update({
      configVersion: config.configVersion,
      maskToken: config.maskToken,
      reloadNow: true,
      items: [{ key: 'ALPHASIFT_ENABLED', value: 'true' }],
    });
    notifyAlphaSiftConfigChanged();
    await this.install();
  },
};
