import type { RuntimeProvider } from '../services/api';
import type { ImageItem } from '../types';
import type { ApiType } from '../store';

export type ProviderId = ImageItem['apiType'] | ApiType | string;

export const BUILTIN_API_ORDER = ['openai', 'cliproxy', 'sousaku', 'nanobanana2', 'apimart'];

export const FALLBACK_PROVIDER_LABELS: Record<string, string> = {
    openai: 'ChatGPT2API',
    cliproxy: 'CLIProxy',
    sousaku: 'Sousaku',
    nanobanana2: 'Nanobanana2',
    apimart: 'APIMart',
    other: 'Other',
};

const FALLBACK_BADGE_CLASS: Record<string, string> = {
    other: 'bg-yellow-500/20 text-yellow-400',
    openai: 'bg-green-500/20 text-green-400',
    nanobanana2: 'bg-purple-500/20 text-purple-400',
    cliproxy: 'bg-red-500/20 text-red-400',
    sousaku: 'bg-cyan-500/20 text-cyan-400',
    apimart: 'bg-blue-500/20 text-blue-400',
};

export function isBuiltinApi(value: string): value is ApiType {
    return BUILTIN_API_ORDER.includes(value);
}

export function providerById(providers: RuntimeProvider[], providerId: ProviderId) {
    const id = String(providerId || '').toLowerCase();
    return providers.find((provider) => provider.id.toLowerCase() === id);
}

export function providerLabel(providerId: ProviderId, providers: RuntimeProvider[] = []) {
    const id = String(providerId || '').toLowerCase();
    return providerById(providers, id)?.label || FALLBACK_PROVIDER_LABELS[id] || String(providerId || '-') || '-';
}

export function providerBadgeClass(providerId: ProviderId) {
    const id = String(providerId || '').toLowerCase();
    return FALLBACK_BADGE_CLASS[id] || 'bg-zinc-500/20 text-zinc-300';
}

export function providerBadgeStyle(providerId: ProviderId, providers: RuntimeProvider[] = []) {
    const id = String(providerId || '').toLowerCase();
    if (isBuiltinApi(id)) return undefined;
    const provider = providerById(providers, providerId);
    const color = provider?.badgeColor;
    if (!color || !/^#[0-9a-fA-F]{6}$/.test(color)) return undefined;
    return {
        color,
        backgroundColor: `${color}26`,
    };
}

export function generationProviderOptions(providers: RuntimeProvider[]) {
    const byId = new Map(providers.map((provider) => [provider.id, provider]));
    const builtinOptions = BUILTIN_API_ORDER
        .map((id) => {
            const provider = byId.get(id);
            if (provider && provider.enabled === false) return null;
            return {
                value: id,
                label: provider?.label || FALLBACK_PROVIDER_LABELS[id],
            };
        })
        .filter((item): item is { value: ApiType; label: string } => Boolean(item));
    const customOptions = providers
        .filter((provider) => provider.enabled !== false && !BUILTIN_API_ORDER.includes(provider.id))
        .map((provider) => ({ value: provider.id, label: provider.label || provider.id }));
    const options = [...builtinOptions, ...customOptions];
    return options.length > 0
        ? options
        : BUILTIN_API_ORDER.map((id) => ({ value: id, label: FALLBACK_PROVIDER_LABELS[id] }));
}

export function importProviderOptions(providers: RuntimeProvider[]) {
    return [
        { value: 'other' as ImageItem['apiType'], label: FALLBACK_PROVIDER_LABELS.other },
        ...BUILTIN_API_ORDER.map((id) => ({
            value: id as ImageItem['apiType'],
            label: providerLabel(id, providers),
        })),
    ];
}
