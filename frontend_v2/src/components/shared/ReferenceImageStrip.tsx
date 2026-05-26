import type { ReferenceImageInput } from '../../types';

function refIdFromUrl(value?: string) {
    const match = String(value || '').match(/\/api\/reference-images\/([a-fA-F0-9]{64})(?:\/thumbnail)?/);
    return match?.[1]?.toLowerCase();
}

function thumbnailSrc(item: ReferenceImageInput) {
    const refId = item.ref_id || refIdFromUrl(item.url);
    if (refId) {
        return `/api/reference-images/${encodeURIComponent(refId)}/thumbnail?w=192`;
    }
    const url = item.public_url || item.url || '';
    if (!url || url.startsWith('data:') || url.length > 500) {
        return '';
    }
    return url;
}

function originalSrc(item: ReferenceImageInput) {
    const refId = item.ref_id || refIdFromUrl(item.url);
    if (refId) {
        return `/api/reference-images/${encodeURIComponent(refId)}`;
    }
    const url = item.public_url || item.url || '';
    return url.startsWith('data:') || url.length > 500 ? '' : url;
}

function referenceLabel(item: ReferenceImageInput, index: number) {
    if (item.name) return item.name;
    if (item.ref_id) return item.ref_id.slice(0, 10);
    const url = item.public_url || item.url || '';
    if (url.startsWith('data:')) return 'inline image';
    if (url) {
        try {
            return new URL(url, window.location.origin).pathname.split('/').pop() || `reference ${index + 1}`;
        } catch {
            return url.slice(0, 24);
        }
    }
    return `reference ${index + 1}`;
}

export function ReferenceImageStrip({
    images,
    emptyText = '无参考图',
    max = 12,
    size = 'md',
}: {
    images?: ReferenceImageInput[];
    emptyText?: string;
    max?: number;
    size?: 'sm' | 'md';
}) {
    const refs = (images || []).filter((item) => Boolean(item?.ref_id || item?.url || item?.public_url));
    if (!refs.length) {
        return <div className="text-sm text-[var(--text-muted)]">{emptyText}</div>;
    }

    const visible = refs.slice(0, max);
    const hidden = Math.max(0, refs.length - visible.length);
    const tileClass = size === 'sm'
        ? 'h-9 w-9 rounded'
        : 'h-14 w-14 rounded-md';
    const hiddenTextClass = size === 'sm' ? 'text-[11px]' : 'text-xs';

    return (
        <div className="flex flex-wrap gap-2">
            {visible.map((item, index) => {
                const src = thumbnailSrc(item);
                const original = originalSrc(item);
                const label = referenceLabel(item, index);
                return (
                    <button
                        key={`${item.ref_id || item.url || item.public_url || index}-${index}`}
                        type="button"
                        disabled={!original}
                        onClick={() => original && window.open(original, '_blank', 'noopener,noreferrer')}
                        title={label}
                        className={`group relative ${tileClass} overflow-hidden border border-[var(--border-subtle)] bg-[var(--bg-secondary)] text-[10px] text-[var(--text-muted)] transition-colors enabled:hover:border-[var(--accent-primary)] disabled:cursor-default`}
                    >
                        {src ? (
                            <img
                                src={src}
                                alt={label}
                                loading="lazy"
                                decoding="async"
                                className="h-full w-full object-cover transition-transform group-enabled:group-hover:scale-105"
                            />
                        ) : (
                            <span className="flex h-full w-full items-center justify-center px-1 text-center leading-tight">inline</span>
                        )}
                    </button>
                );
            })}
            {hidden > 0 && (
                <div className={`flex ${tileClass} items-center justify-center border border-[var(--border-subtle)] bg-[var(--bg-secondary)] ${hiddenTextClass} text-[var(--text-secondary)]`}>
                    +{hidden}
                </div>
            )}
        </div>
    );
}
