import {
    Activity,
    Check,
    ChevronDown,
    Database,
    Trash2,
    FolderOpen,
    HardDrive,
    Lock,
    Loader2,
    Plus,
    Plug,
    RotateCw,
    Settings2,
    Shield,
    SlidersHorizontal,
    Sparkles,
    Wifi,
} from 'lucide-react';
import type { ComponentType, ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useNavigate, useParams } from 'react-router-dom';
import { HexColorPicker } from 'react-colorful';
import {
    clearStorageCache,
    createRuntimeProvider,
    deleteRuntimeProvider,
    loadBackendSettings,
    loadRuntimeProviders,
    loadStorageUsage,
    saveBackendSettings,
    saveRuntimeProvider,
} from '../../services/api';
import type { BackendSettings, RuntimeProvider, SettingValue, StorageUsage } from '../../services/api';
import {
    DEFAULT_GALLERY_SELECTION_BOX_COLOR,
    DEFAULT_GALLERY_SELECTION_COLOR,
    DEFAULT_GALLERY_TAG_COLOR,
    MAX_GALLERY_PAGE_SIZE,
    MIN_GALLERY_PAGE_SIZE,
    useStore,
} from '../../store';
import type { GalleryColumnSize, GalleryDisplayMode } from '../../store';
import { notifyProvidersUpdated } from '../../hooks/useProviders';

type SectionId = 'preferences' | 'providers' | 'jobs' | 'storage' | 'network' | 'advanced';

interface SectionDef {
    id: SectionId;
    label: string;
    icon: ComponentType<{ className?: string }>;
}

const SECTIONS: SectionDef[] = [
    { id: 'preferences', label: '偏好', icon: SlidersHorizontal },
    { id: 'providers', label: 'Provider', icon: Plug },
    { id: 'jobs', label: '任务', icon: Activity },
    { id: 'storage', label: '存储', icon: HardDrive },
    { id: 'network', label: '网络', icon: Wifi },
    { id: 'advanced', label: '高级', icon: Settings2 },
];

const COLUMN_OPTIONS: Array<{ value: GalleryColumnSize; label: string }> = [
    { value: 7, label: '小' },
    { value: 6, label: '中' },
    { value: 5, label: '大' },
];

const DISPLAY_MODE_OPTIONS: Array<{ value: GalleryDisplayMode; label: string }> = [
    { value: 'waterfall', label: '瀑布流' },
    { value: 'pagination', label: '分页' },
];

const ACCENT_COLOR_PRESETS = ['#ff8a00', '#ffb703', '#e76f51', '#2a9d8f', '#8ecae6', '#cdb4db'];
const SELECTION_BOX_COLOR_PRESETS = ['#fff3b0', '#ffd6a5', '#ffc2b4', '#b7e4c7', '#bde0fe', '#e0bbe4'];
const TAG_COLOR_PRESETS = ['#f43f5e', '#f97316', '#10b981', '#06b6d4', '#6366f1', '#d946ef'];
const CUSTOM_PROVIDER_BADGE_COLOR = '#8ecae6';
const OPENAI_PROTOCOL_OPTIONS = [
    { value: 'images', label: 'Images API' },
    { value: 'chat-completions', label: 'Chat Completions' },
    { value: 'responses', label: 'Responses' },
];

type ModelExposureKey = 'ratio' | 'resolution' | 'quality' | 'imageCount';

type ExposureOption = string | number | boolean | { value: string | number | boolean; label?: string };

type ExposureFieldDraft = {
    enabled: boolean;
    key: ModelExposureKey;
    label: string;
    requestField: string;
    type: 'select' | 'boolean';
    optionsText: string;
    defaultValue: string;
};

type ModelDraft = {
    label: string;
    controls: Record<ModelExposureKey, ExposureFieldDraft>;
};

type RuntimeModelControl = NonNullable<RuntimeProvider['models'][number]['controls']>[number];

const EXPOSURE_ORDER: ModelExposureKey[] = ['ratio', 'resolution', 'quality', 'imageCount'];
const EXPOSURE_REQUEST_FIELDS: Record<ModelExposureKey, string> = {
    ratio: 'size',
    resolution: 'resolution',
    quality: 'quality',
    imageCount: 'n',
};
const EXPOSURE_LABELS: Record<ModelExposureKey, string> = {
    ratio: '比例 / 尺寸',
    resolution: '分辨率',
    quality: '质量',
    imageCount: '数量',
};
const EXPOSURE_DEFAULTS: Record<ModelExposureKey, ExposureFieldDraft> = {
    ratio: {
        enabled: true,
        key: 'ratio',
        label: '比例 / 尺寸',
        requestField: 'size',
        type: 'select',
        optionsText: '16:9; 9:16; 1:1; 4:3; 3:4; 1080x1920; 1920x1080',
        defaultValue: '16:9',
    },
    resolution: {
        enabled: true,
        key: 'resolution',
        label: '分辨率',
        requestField: 'resolution',
        type: 'select',
        optionsText: '1K; 2K; 4K',
        defaultValue: '2K',
    },
    quality: {
        enabled: true,
        key: 'quality',
        label: '质量',
        requestField: 'quality',
        type: 'select',
        optionsText: 'low|Low; medium|Medium; high|High',
        defaultValue: 'high',
    },
    imageCount: {
        enabled: true,
        key: 'imageCount',
        label: '数量',
        requestField: 'n',
        type: 'select',
        optionsText: '1; 2; 3; 4',
        defaultValue: '1',
    },
};

function sourceText(source?: string) {
    return source || 'config.py';
}

function formatBytes(bytes?: number) {
    const value = Math.max(0, Number(bytes || 0));
    if (value < 1024) return `${value} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let size = value / 1024;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex += 1;
    }
    return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[unitIndex]}`;
}

function isHexColor(value: string) {
    return /^#[0-9a-fA-F]{6}$/.test(value.trim());
}

function SectionHeader({
    title,
    subtitle,
    actions,
}: {
    title: string;
    subtitle?: string;
    actions?: ReactNode;
}) {
    return (
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
            <div>
                <h2 className="text-xl font-semibold text-[var(--text-primary)]">{title}</h2>
                {subtitle && <p className="mt-1 text-sm text-[var(--text-muted)]">{subtitle}</p>}
            </div>
            {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
    );
}

function SourceBadge({ source }: { source?: string }) {
    return (
        <span className="rounded-full border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]">
            {sourceText(source)}
        </span>
    );
}

function Panel({ children }: { children: ReactNode }) {
    return (
        <section className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-5">
            {children}
        </section>
    );
}

function SettingRow({
    label,
    value,
    source,
}: {
    label: string;
    value: ReactNode;
    source?: string;
}) {
    return (
        <div className="grid gap-2 border-b border-[var(--border-subtle)] py-3 last:border-b-0 md:grid-cols-[11rem_1fr_auto] md:items-center">
            <div className="text-sm font-medium text-[var(--text-secondary)]">{label}</div>
            <div className="min-w-0 text-sm text-[var(--text-primary)]">{value}</div>
            <SourceBadge source={source} />
        </div>
    );
}

function Toggle({
    checked,
    onChange,
    label,
}: {
    checked: boolean;
    onChange: (value: boolean) => void;
    label: string;
}) {
    return (
        <button type="button" onClick={() => onChange(!checked)} className="flex items-center gap-3 text-left" aria-pressed={checked}>
            <span
                className={`relative h-6 w-11 rounded-full border transition-colors ${
                    checked
                        ? 'border-[var(--accent-primary)]/45 bg-[var(--accent-primary)]/20'
                        : 'border-[var(--border-subtle)] bg-[var(--bg-secondary)]'
                }`}
            >
                <span
                    className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full shadow transition-all ${
                        checked ? 'translate-x-5 bg-[var(--accent-primary)]' : 'translate-x-0 bg-[var(--text-muted)]'
                    }`}
                />
            </span>
            <span className="text-sm text-[var(--text-primary)]">{label}</span>
        </button>
    );
}

function FieldLabel({ children, locked = false }: { children: ReactNode; locked?: boolean }) {
    return (
        <span className="mb-2 flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
            {children}
            {locked && <Lock className="h-3.5 w-3.5 text-[var(--text-muted)]" aria-label="锁定" />}
        </span>
    );
}

function fieldClassName(locked = false, extra = '') {
    const stateClass = locked
        ? 'cursor-not-allowed border-dashed text-[var(--text-muted)]'
        : 'text-[var(--text-primary)] focus:border-[var(--accent-primary)]';
    return `w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm outline-none ${stateClass} ${extra}`;
}

function ColorPreference({
    label,
    value,
    onChange,
    defaultValue,
}: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    defaultValue: string;
}) {
    const colorInputValue = isHexColor(value) ? value : defaultValue;
    const [pickerOpen, setPickerOpen] = useState(false);
    const pickerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!pickerOpen) return;

        const handlePointerDown = (event: PointerEvent) => {
            if (!pickerRef.current?.contains(event.target as Node)) {
                setPickerOpen(false);
            }
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') setPickerOpen(false);
        };

        window.addEventListener('pointerdown', handlePointerDown);
        window.addEventListener('keydown', handleKeyDown);
        return () => {
            window.removeEventListener('pointerdown', handlePointerDown);
            window.removeEventListener('keydown', handleKeyDown);
        };
    }, [pickerOpen]);

    return (
        <div className="relative" ref={pickerRef}>
            <div className="mb-2 flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)]">{label}</span>
                <button
                    type="button"
                    onClick={() => setPickerOpen((open) => !open)}
                    className="h-6 w-6 rounded-md border border-white/10 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.18)] transition-transform hover:scale-105"
                    style={{ backgroundColor: colorInputValue }}
                    aria-label={`自定义${label}`}
                />
                <button
                    type="button"
                    onClick={() => onChange(defaultValue)}
                    className="rounded-md border border-[var(--border-subtle)] px-2 py-1 text-xs text-[var(--text-muted)] transition-colors hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]"
                >
                    恢复默认
                </button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <input
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    className="h-8 w-24 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2 font-mono text-xs text-[var(--text-secondary)] outline-none focus:border-[var(--accent-primary)]"
                />
            </div>
            {pickerOpen && (
                <div className="absolute left-0 top-[3.7rem] z-[90] w-64 rounded-xl border border-[var(--border-subtle)] bg-[rgba(24,24,27,0.98)] p-3 shadow-[0_18px_48px_rgba(0,0,0,0.42)] backdrop-blur-xl">
                    <div className="mb-3 flex items-center justify-between gap-3">
                        <span className="text-xs font-medium text-[var(--text-secondary)]">自定义颜色</span>
                        <span className="h-5 w-5 rounded border border-white/10" style={{ backgroundColor: colorInputValue }} />
                    </div>
                    <HexColorPicker color={colorInputValue} onChange={onChange} className="proxy-color-picker" />
                    <div className="mt-3 flex items-center gap-2">
                        <input
                            value={value}
                            onChange={(event) => onChange(event.target.value)}
                            className="h-8 min-w-0 flex-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2 font-mono text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        />
                        <button
                            type="button"
                            onClick={() => setPickerOpen(false)}
                            className="h-8 rounded-md bg-[var(--bg-card-hover)] px-2.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        >
                            完成
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

function ModelExposureEditor({
    modelId,
    draft,
    onChange,
    onRemove,
}: {
    modelId: string;
    draft: ModelDraft;
    onChange: (next: ModelDraft) => void;
    onRemove: () => void;
}) {
    const [open, setOpen] = useState(false);
    const updateControl = useCallback((key: ModelExposureKey, patch: Partial<ExposureFieldDraft>) => {
        onChange({
            ...draft,
            controls: {
                ...draft.controls,
                [key]: {
                    ...draft.controls[key],
                    ...patch,
                },
            },
        });
    }, [draft, onChange]);

    return (
        <div className="overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
            <div className="flex min-h-14 items-center gap-2 px-3 py-2">
                <button
                    type="button"
                    onClick={() => setOpen((value) => !value)}
                    className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-[var(--bg-card-hover)]"
                    aria-expanded={open}
                >
                    <ChevronDown className={`h-4 w-4 shrink-0 text-[var(--text-muted)] transition-transform ${open ? '' : '-rotate-90'}`} />
                    <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-[var(--text-primary)]">{draft.label || modelId}</span>
                        <span className="block truncate font-mono text-xs text-[var(--text-muted)]">{modelId}</span>
                    </span>
                </button>
                <button
                    type="button"
                    onClick={onRemove}
                    className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-2.5 text-xs text-[var(--text-secondary)] transition-colors hover:border-red-400/50 hover:bg-red-500/10 hover:text-red-200"
                >
                    <Trash2 className="h-3.5 w-3.5" />
                    移除
                </button>
            </div>

            {open && (
                <div className="border-t border-[var(--border-subtle)] p-4">
                    <label className="mb-4 block">
                        <span className="mb-1 block text-xs text-[var(--text-muted)]">模型显示名</span>
                        <input
                            value={draft.label}
                            onChange={(event) => onChange({ ...draft, label: event.target.value })}
                            placeholder={modelId}
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        />
                    </label>

                    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)]">
                        {EXPOSURE_ORDER.map((key) => {
                            const control = draft.controls[key];
                            const isSelect = control.type === 'select';
                            return (
                                <div
                                    key={key}
                                    className="grid gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-card)] p-3 last:border-b-0 xl:grid-cols-[8rem_minmax(0,1fr)_8rem_8rem] xl:items-end"
                                >
                                    <label className="flex min-h-9 items-center gap-2 text-sm text-[var(--text-primary)]">
                                        <input
                                            type="checkbox"
                                            checked={control.enabled}
                                            onChange={(event) => updateControl(key, { enabled: event.target.checked })}
                                            className="h-4 w-4 accent-[var(--accent-primary)]"
                                        />
                                        <span>{EXPOSURE_LABELS[key]}</span>
                                    </label>
                                    {isSelect ? (
                                        <>
                                            <label className="block">
                                                <span className="mb-1 block text-xs text-[var(--text-muted)]">候选值</span>
                                                <input
                                                    value={control.optionsText}
                                                    onChange={(event) => updateControl(key, { optionsText: event.target.value })}
                                                    disabled={!control.enabled}
                                                    placeholder="high; medium; low"
                                                    className="w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2.5 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-45"
                                                />
                                            </label>
                                            <label className="block">
                                                <span className="mb-1 block text-xs text-[var(--text-muted)]">默认</span>
                                                <input
                                                    value={control.defaultValue}
                                                    onChange={(event) => updateControl(key, { defaultValue: event.target.value })}
                                                    disabled={!control.enabled}
                                                    className="w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2.5 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-45"
                                                />
                                            </label>
                                            <label className="block">
                                                <span className="mb-1 block text-xs text-[var(--text-muted)]">字段</span>
                                                <input
                                                    value={control.requestField}
                                                    onChange={(event) => updateControl(key, { requestField: event.target.value })}
                                                    disabled={!control.enabled}
                                                    placeholder={EXPOSURE_REQUEST_FIELDS[key]}
                                                    className="w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2.5 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-45"
                                                />
                                            </label>
                                        </>
                                    ) : (
                                        <div className="xl:col-span-3">
                                            <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-xs text-[var(--text-muted)]">
                                                勾选后底部面板允许使用这个能力。
                                            </div>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    <div className="mt-3 text-xs text-[var(--text-muted)]">
                        候选值支持逗号、分号或换行；`value|显示名` 可以把发送值和界面显示分开。
                    </div>
                </div>
            )}
        </div>
    );
}

interface ColorTarget {
    id: string;
    label: string;
    value: string;
    onChange: (value: string) => void;
    presets: string[];
}

function SharedColorPalette({ targets }: { targets: ColorTarget[] }) {
    const [activeTargetId, setActiveTargetId] = useState(targets[0]?.id || '');
    const activeTarget = targets.find((target) => target.id === activeTargetId) || targets[0];

    if (!activeTarget) return null;

    return (
        <div className="space-y-3">
            <div className="inline-flex flex-wrap gap-1 rounded-lg bg-[var(--bg-secondary)] p-1">
                {targets.map((target) => (
                    <button
                        key={target.id}
                        type="button"
                        onClick={() => setActiveTargetId(target.id)}
                        className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                            activeTarget.id === target.id
                                ? 'bg-[var(--bg-card-hover)] text-[var(--text-primary)] shadow-sm'
                                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                        }`}
                    >
                        {target.label}
                    </button>
                ))}
            </div>
            <div className="flex flex-wrap gap-2">
                {activeTarget.presets.map((color) => (
                    <button
                        key={color}
                        type="button"
                        onClick={() => activeTarget.onChange(color)}
                        className={`h-8 w-8 rounded-md border shadow-[inset_0_0_0_1px_rgba(0,0,0,0.18)] transition-transform hover:scale-105 ${
                            activeTarget.value.toLowerCase() === color.toLowerCase()
                                ? 'border-white/80 ring-2 ring-white/18'
                                : 'border-white/15'
                        }`}
                        style={{ backgroundColor: color }}
                        aria-label={`${activeTarget.label} ${color}`}
                    />
                ))}
            </div>
        </div>
    );
}

function PathValue({ value }: { value?: SettingValue<string> }) {
    if (!value) return <span>-</span>;
    return (
        <div className="min-w-0">
            <div className="truncate font-mono text-xs text-[var(--text-primary)]">{value.resolved || value.value}</div>
            {value.resolved && value.resolved !== value.value && (
                <div className="mt-1 truncate font-mono text-[11px] text-[var(--text-muted)]">{value.value}</div>
            )}
        </div>
    );
}

function PreferencesPanel() {
    const autoClearPrompt = useStore((s) => s.autoClearPrompt);
    const setAutoClearPrompt = useStore((s) => s.setAutoClearPrompt);
    const galleryColumns = useStore((s) => s.galleryColumns);
    const setGalleryColumns = useStore((s) => s.setGalleryColumns);
    const galleryDisplayMode = useStore((s) => s.galleryDisplayMode);
    const setGalleryDisplayMode = useStore((s) => s.setGalleryDisplayMode);
    const galleryPageSize = useStore((s) => s.galleryPageSize);
    const setGalleryPageSize = useStore((s) => s.setGalleryPageSize);
    const deleteLocalFile = useStore((s) => s.deleteLocalFile);
    const setDeleteLocalFile = useStore((s) => s.setDeleteLocalFile);
    const deleteImportedOriginal = useStore((s) => s.deleteImportedOriginal);
    const setDeleteImportedOriginal = useStore((s) => s.setDeleteImportedOriginal);
    const gallerySelectionColor = useStore((s) => s.gallerySelectionColor);
    const setGallerySelectionColor = useStore((s) => s.setGallerySelectionColor);
    const gallerySelectionBoxColor = useStore((s) => s.gallerySelectionBoxColor);
    const setGallerySelectionBoxColor = useStore((s) => s.setGallerySelectionBoxColor);
    const galleryTagColor = useStore((s) => s.galleryTagColor);
    const setGalleryTagColor = useStore((s) => s.setGalleryTagColor);
    const [galleryPageSizeDraft, setGalleryPageSizeDraft] = useState(String(galleryPageSize));

    useEffect(() => {
        setGalleryPageSizeDraft(String(galleryPageSize));
    }, [galleryPageSize]);

    const commitGalleryPageSize = useCallback(() => {
        const trimmed = galleryPageSizeDraft.trim();
        if (!trimmed) {
            setGalleryPageSizeDraft(String(galleryPageSize));
            return;
        }
        const parsed = Number(trimmed);
        if (!Number.isFinite(parsed)) {
            setGalleryPageSizeDraft(String(galleryPageSize));
            return;
        }
        setGalleryPageSize(parsed);
    }, [galleryPageSize, galleryPageSizeDraft, setGalleryPageSize]);

    const colorTargets = useMemo<ColorTarget[]>(() => [
        {
            id: 'selection',
            label: '选中边框',
            value: gallerySelectionColor,
            onChange: setGallerySelectionColor,
            presets: ACCENT_COLOR_PRESETS,
        },
        {
            id: 'selection-box',
            label: '框选区域',
            value: gallerySelectionBoxColor,
            onChange: setGallerySelectionBoxColor,
            presets: SELECTION_BOX_COLOR_PRESETS,
        },
        {
            id: 'tag',
            label: 'TAG',
            value: galleryTagColor,
            onChange: setGalleryTagColor,
            presets: TAG_COLOR_PRESETS,
        },
    ], [
        gallerySelectionBoxColor,
        gallerySelectionColor,
        galleryTagColor,
        setGallerySelectionBoxColor,
        setGallerySelectionColor,
        setGalleryTagColor,
    ]);

    return (
        <div>
            <SectionHeader title="偏好" subtitle="本地图廊显示、生成行为和外观颜色" />
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(22rem,0.95fr)]">
                <Panel>
                    <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                        <FolderOpen className="h-4 w-4 text-[var(--accent-primary)]" />
                        图廊
                    </div>
                    <div className="space-y-5">
                        <label className="block">
                            <span className="mb-2 block text-xs text-[var(--text-muted)]">分页每页张数</span>
                            <input
                                type="number"
                                min={MIN_GALLERY_PAGE_SIZE}
                                max={MAX_GALLERY_PAGE_SIZE}
                                step={10}
                                value={galleryPageSizeDraft}
                                onChange={(event) => setGalleryPageSizeDraft(event.target.value)}
                                onBlur={commitGalleryPageSize}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter') {
                                        event.currentTarget.blur();
                                    }
                                    if (event.key === 'Escape') {
                                        setGalleryPageSizeDraft(String(galleryPageSize));
                                        event.currentTarget.blur();
                                    }
                                }}
                                className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                            />
                            <span className="mt-1 block text-xs text-[var(--text-muted)]">
                                范围 {MIN_GALLERY_PAGE_SIZE}-{MAX_GALLERY_PAGE_SIZE}，仅分页模式生效
                            </span>
                        </label>
                        <div>
                            <div className="mb-2 text-xs text-[var(--text-muted)]">缩略图大小</div>
                            <div className="flex overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
                                {COLUMN_OPTIONS.map((option) => (
                                    <button
                                        key={option.value}
                                        type="button"
                                        onClick={() => setGalleryColumns(option.value)}
                                        className={`flex-1 px-3 py-2 text-sm transition-colors ${
                                            galleryColumns === option.value
                                                ? 'bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]'
                                                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]'
                                        }`}
                                    >
                                        {option.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div>
                            <div className="mb-2 text-xs text-[var(--text-muted)]">浏览方式</div>
                            <div className="flex overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
                                {DISPLAY_MODE_OPTIONS.map((option) => (
                                    <button
                                        key={option.value}
                                        type="button"
                                        onClick={() => setGalleryDisplayMode(option.value)}
                                        className={`flex-1 px-3 py-2 text-sm transition-colors ${
                                            galleryDisplayMode === option.value
                                                ? 'bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]'
                                                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]'
                                        }`}
                                    >
                                        {option.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <Toggle checked={deleteLocalFile} onChange={setDeleteLocalFile} label="删除记录时删除本地文件" />
                        <Toggle checked={deleteImportedOriginal} onChange={setDeleteImportedOriginal} label="导入成功后删除源文件" />
                    </div>
                </Panel>

                <div className="space-y-4">
                    <Panel>
                        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                            <Sparkles className="h-4 w-4 text-[var(--accent-primary)]" />
                            生成行为
                        </div>
                        <Toggle checked={autoClearPrompt} onChange={setAutoClearPrompt} label="生成后自动清除提示词" />
                    </Panel>

                    <Panel>
                        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                            <Check className="h-4 w-4 text-[var(--accent-primary)]" />
                            外观颜色
                        </div>
                        <div className="space-y-4">
                            <SharedColorPalette targets={colorTargets} />
                            <div className="grid gap-4 md:grid-cols-3">
                                <ColorPreference
                                    label="选中边框颜色"
                                    value={gallerySelectionColor}
                                    onChange={setGallerySelectionColor}
                                    defaultValue={DEFAULT_GALLERY_SELECTION_COLOR}
                                />
                                <ColorPreference
                                    label="框选区域颜色"
                                    value={gallerySelectionBoxColor}
                                    onChange={setGallerySelectionBoxColor}
                                    defaultValue={DEFAULT_GALLERY_SELECTION_BOX_COLOR}
                                />
                                <ColorPreference
                                    label="TAG 颜色"
                                    value={galleryTagColor}
                                    onChange={setGalleryTagColor}
                                    defaultValue={DEFAULT_GALLERY_TAG_COLOR}
                                />
                            </div>
                        </div>
                    </Panel>
                </div>
            </div>
        </div>
    );
}

function ProviderPanel({
    providers,
    activeProviderId,
    onReload,
}: {
    providers: RuntimeProvider[];
    activeProviderId?: string;
    onReload: () => Promise<void>;
}) {
    const isCreatingCustom = activeProviderId === 'custom';
    const activeProvider = isCreatingCustom
        ? undefined
        : providers.find((provider) => provider.id === activeProviderId) || providers[0];
    return (
        <div className="grid gap-4 xl:grid-cols-[16rem_1fr]">
            <Panel>
                <div className="mb-3 flex items-center justify-between gap-2">
                    <div className="text-sm font-semibold text-[var(--text-primary)]">Provider 列表</div>
                </div>
                <div className="space-y-1">
                    {providers.map((provider) => (
                        <NavLink
                            key={provider.id}
                            to={`/settings/providers/${provider.id}`}
                            className={({ isActive }) =>
                                [
                                    'block rounded-lg px-3 py-2 text-sm transition-colors',
                                    isActive
                                        ? 'bg-[var(--accent-primary)]/18 text-[var(--text-primary)]'
                                        : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]',
                                ].join(' ')
                            }
                        >
                            <div className="font-medium">{provider.label}</div>
                            <div className="mt-0.5 truncate text-xs text-[var(--text-muted)]">{provider.type}</div>
                        </NavLink>
                    ))}
                    <NavLink
                        to="/settings/providers/custom"
                        className={({ isActive }) =>
                            [
                                'mt-3 flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm transition-colors',
                                isActive || isCreatingCustom
                                    ? 'border-[var(--accent-primary)]/50 bg-[var(--accent-primary)]/14 text-[var(--text-primary)]'
                                    : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]',
                            ].join(' ')
                        }
                    >
                        <Plus className="h-4 w-4" />
                        <span className="font-medium">自定义 API</span>
                    </NavLink>
                </div>
            </Panel>

            {isCreatingCustom ? (
                <CustomProviderForm onReload={onReload} />
            ) : activeProvider ? (
                <ProviderDetails provider={activeProvider} onReload={onReload} />
            ) : null}
        </div>
    );
}

function slugFromProvider(label: string, baseUrl: string) {
    const source = label.trim() || baseUrl.trim() || 'custom-api';
    const withoutProtocol = source.replace(/^https?:\/\//i, '');
    const slug = withoutProtocol.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
    return slug || `custom-api-${Date.now().toString(36)}`;
}

function modelListFromText(text: string) {
    return text
        .split(/[;,\n]+/)
        .map((item) => item.trim())
        .filter(Boolean);
}

function parseExposureOptions(text: string): ExposureOption[] {
    return text
        .split(/[;,\n]+/)
        .map((item) => item.trim())
        .filter(Boolean)
        .map((item) => {
            const separatorIndex = item.indexOf('|');
            if (separatorIndex > 0) {
                const value = item.slice(0, separatorIndex).trim();
                const label = item.slice(separatorIndex + 1).trim();
                if (value) {
                    return label ? { value, label } : value;
                }
            }
            return item;
        });
}

function exposureOptionsToText(options: ExposureOption[] | undefined) {
    if (!Array.isArray(options)) return '';
    return options
        .map((option) => {
            if (typeof option === 'object' && option) {
                return option.label ? `${String(option.value)}|${option.label}` : String(option.value);
            }
            return String(option);
        })
        .join('; ');
}

function createExposureDraft(key: ModelExposureKey): ExposureFieldDraft {
    const preset = EXPOSURE_DEFAULTS[key];
    return { ...preset };
}

function createModelDraft(modelValue: string, model?: RuntimeProvider['models'][number]): ModelDraft {
    const payload = model?.payload && typeof model.payload === 'object' ? model.payload : {};
    const controlsByKey = new Map((model?.controls || []).map((control) => [String(control.key), control]));
    const isExistingModel = Boolean(model);
    const draft: ModelDraft = {
        label: model?.label || modelValue,
        controls: {
            ratio: { ...createExposureDraft('ratio'), enabled: !isExistingModel },
            resolution: { ...createExposureDraft('resolution'), enabled: !isExistingModel },
            quality: { ...createExposureDraft('quality'), enabled: !isExistingModel },
            imageCount: { ...createExposureDraft('imageCount'), enabled: !isExistingModel },
        },
    };

    for (const key of EXPOSURE_ORDER) {
        const control = controlsByKey.get(key);
        if (control) {
            const mappedField = Object.entries(payload).find(([, sourceKey]) => String(sourceKey) === key)?.[0];
            draft.controls[key] = {
                ...draft.controls[key],
                enabled: true,
                label: control.label || EXPOSURE_LABELS[key],
                type: control.type === 'boolean' ? 'boolean' : 'select',
                optionsText: exposureOptionsToText(control.options as ExposureOption[] | undefined) || draft.controls[key].optionsText,
                defaultValue: String(model?.defaults?.[key] ?? draft.controls[key].defaultValue ?? ''),
                requestField: key === 'imageCount' && mappedField === 'count'
                    ? 'n'
                    : mappedField || EXPOSURE_REQUEST_FIELDS[key],
            };
        }
    }

    return draft;
}

function buildModelConfig(modelValue: string, draft?: ModelDraft) {
    const controls: RuntimeModelControl[] = [];
    const defaults: Record<string, unknown> = {};
    const features: Record<string, unknown> = { referenceImage: true, mask: false };
    const payload: Record<string, unknown> = {};
    const source = draft || createModelDraft(modelValue);

    for (const key of EXPOSURE_ORDER) {
        const control = source.controls[key];
        if (!control?.enabled) continue;

        const requestField = control.requestField.trim() || EXPOSURE_REQUEST_FIELDS[key];
        const label = control.label.trim() || EXPOSURE_LABELS[key];
        const nextControl: Record<string, unknown> = {
            key,
            label,
            type: control.type,
        };
        if (control.type === 'select') {
            nextControl.options = parseExposureOptions(control.optionsText);
            if (control.defaultValue.trim()) {
                defaults[key] = control.defaultValue.trim();
            }
        }
        controls.push(nextControl as RuntimeModelControl);
        if (requestField) {
            payload[requestField] = key;
        }
    }

    return {
        value: modelValue,
        label: source.label.trim() || modelValue,
        defaults,
        controls,
        features,
        payload,
    };
}

function CustomProviderForm({ onReload }: { onReload: () => Promise<void> }) {
    const navigate = useNavigate();
    const [adding, setAdding] = useState(false);
    const [provider, setProvider] = useState({
        label: '',
        protocol: 'images',
        baseUrl: '',
        apiKey: '',
        badgeColor: CUSTOM_PROVIDER_BADGE_COLOR,
        modelsText: 'gpt-image-2',
        stream: false,
    });
    const [modelDrafts, setModelDrafts] = useState<Record<string, ModelDraft>>({});
    const modelIds = useMemo(() => modelListFromText(provider.modelsText), [provider.modelsText]);

    useEffect(() => {
        setModelDrafts((current) => {
            const next = { ...current };
            let changed = false;
            for (const modelId of modelIds) {
                if (!next[modelId]) {
                    next[modelId] = createModelDraft(modelId);
                    changed = true;
                }
            }
            for (const modelId of Object.keys(next)) {
                if (!modelIds.includes(modelId)) {
                    delete next[modelId];
                    changed = true;
                }
            }
            return changed ? next : current;
        });
    }, [modelIds]);

    const handleCreate = useCallback(async () => {
        if (adding) return;
        const id = slugFromProvider(provider.label, provider.baseUrl);
        const baseUrl = provider.baseUrl.trim().replace(/\/(?:chat\/completions|responses|images\/generations|images\/edits)\/?$/, '');
        if (!id || !baseUrl || !modelIds.length) return;
        setAdding(true);
        try {
            const models = modelIds.map((modelId) => buildModelConfig(modelId, modelDrafts[modelId]));
            const created = await createRuntimeProvider({
                id,
                label: provider.label.trim() || id,
                type: 'openai-compatible',
                protocol: provider.protocol,
                baseUrl,
                apiKey: provider.apiKey.trim(),
                badgeColor: provider.badgeColor,
                models,
                stream: provider.stream,
            });
            notifyProvidersUpdated();
            await onReload();
            navigate(`/settings/providers/${created.id}`, { replace: true });
        } finally {
            setAdding(false);
        }
    }, [adding, modelDrafts, modelIds, navigate, onReload, provider]);

    return (
        <div>
            <SectionHeader title="自定义 API" subtitle="添加 OpenAI 协议兼容的图片生成接口" />
            <Panel>
                <div className="grid gap-4 xl:grid-cols-2">
                    <label className="block">
                        <span className="mb-2 block text-xs text-[var(--text-muted)]">显示名称</span>
                        <input
                            value={provider.label}
                            onChange={(event) => setProvider((value) => ({ ...value, label: event.target.value }))}
                            placeholder="HMM API"
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        />
                    </label>
                    <label className="block">
                        <span className="mb-2 block text-xs text-[var(--text-muted)]">类型</span>
                        <input
                            value="openai-compatible"
                            readOnly
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-muted)] outline-none"
                        />
                    </label>
                    <div className="block">
                        <ColorPreference
                            label="标签配色"
                            value={provider.badgeColor}
                            onChange={(badgeColor) => setProvider((value) => ({ ...value, badgeColor }))}
                            defaultValue={CUSTOM_PROVIDER_BADGE_COLOR}
                        />
                    </div>
                    <label className="block">
                        <span className="mb-2 block text-xs text-[var(--text-muted)]">协议</span>
                        <select
                            value={provider.protocol}
                            onChange={(event) => setProvider((value) => ({ ...value, protocol: event.target.value, stream: event.target.value === 'chat-completions' ? value.stream : false }))}
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        >
                            {OPENAI_PROTOCOL_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </label>
                    <label className="block xl:col-span-2">
                        <span className="mb-2 block text-xs text-[var(--text-muted)]">Base URL</span>
                        <input
                            value={provider.baseUrl}
                            onChange={(event) => setProvider((value) => ({ ...value, baseUrl: event.target.value }))}
                            placeholder="https://example.com/v1"
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        />
                    </label>
                    <label className="block xl:col-span-2">
                        <span className="mb-2 block text-xs text-[var(--text-muted)]">添加模型</span>
                        <textarea
                            value={provider.modelsText}
                            onChange={(event) => setProvider((value) => ({ ...value, modelsText: event.target.value }))}
                            rows={3}
                            placeholder="gpt-image-2, model-a; model-b&#10;支持逗号、分号或换行分隔"
                            className="w-full resize-none rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        />
                    </label>
                    <label className="block xl:col-span-2">
                        <span className="mb-2 block text-xs text-[var(--text-muted)]">API Key</span>
                        <input
                            value={provider.apiKey}
                            onChange={(event) => setProvider((value) => ({ ...value, apiKey: event.target.value }))}
                            placeholder="sk-your-token"
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                        />
                    </label>
                </div>
                {modelIds.length > 0 && (
                    <div className="mt-5 space-y-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <div className="text-sm font-medium text-[var(--text-primary)]">模型列表</div>
                                <div className="text-xs text-[var(--text-muted)]">点击模型展开，配置要显示的控件和请求字段。</div>
                            </div>
                            {provider.protocol === 'chat-completions' && (
                                <Toggle checked={provider.stream} onChange={(stream) => setProvider((value) => ({ ...value, stream }))} label="使用流式响应" />
                            )}
                        </div>
                        <div className="space-y-4">
                            {modelIds.map((modelId) => (
                                <ModelExposureEditor
                                    key={modelId}
                                    modelId={modelId}
                                    draft={modelDrafts[modelId] || createModelDraft(modelId)}
                                    onChange={(next) => setModelDrafts((current) => ({ ...current, [modelId]: next }))}
                                    onRemove={() => setProvider((value) => ({
                                        ...value,
                                        modelsText: modelListFromText(value.modelsText).filter((item) => item !== modelId).join('\n'),
                                    }))}
                                />
                            ))}
                        </div>
                    </div>
                )}
                <div className="mt-5 flex justify-end">
                    <button
                        type="button"
                        onClick={() => void handleCreate()}
                        disabled={adding || !provider.label.trim() || !provider.baseUrl.trim() || modelIds.length === 0}
                        className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {adding ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        保存
                    </button>
                </div>
            </Panel>
        </div>
    );
}

function ProviderDetails({ provider, onReload }: { provider: RuntimeProvider; onReload: () => Promise<void> }) {
    const isSousaku = provider.type === 'sousaku';
    const isOpenAICompatible = provider.type === 'openai-compatible';
    const isBuiltIn = Boolean(provider.builtin);
    const canEditIdentity = !isBuiltIn;
    const canEditConnection = !isSousaku;
    const canEditCustomSchema = !isBuiltIn && !isSousaku;
    const canDelete = !provider.builtin;
    const navigate = useNavigate();
    const [label, setLabel] = useState(provider.label);
    const [enabled, setEnabled] = useState(provider.enabled);
    const [protocol, setProtocol] = useState(provider.protocol || 'images');
    const [baseUrl, setBaseUrl] = useState(provider.baseUrl);
    const [apiKey, setApiKey] = useState(provider.apiKey || '');
    const [modelsText, setModelsText] = useState((provider.models || []).map((model) => model.value).join('\n'));
    const [badgeColor, setBadgeColor] = useState(provider.badgeColor || CUSTOM_PROVIDER_BADGE_COLOR);
    const [stream, setStream] = useState(Boolean(provider.stream));
    const [notes, setNotes] = useState(provider.notes || '');
    const [saving, setSaving] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [modelDrafts, setModelDrafts] = useState<Record<string, ModelDraft>>({});
    const modelIds = useMemo(() => modelListFromText(modelsText), [modelsText]);

    useEffect(() => {
        setLabel(provider.label);
        setEnabled(provider.enabled);
        setProtocol(provider.protocol || 'images');
        setBaseUrl(provider.baseUrl);
        setApiKey(provider.apiKey || '');
        setModelsText((provider.models || []).map((model) => model.value).join('\n'));
        setBadgeColor(provider.badgeColor || CUSTOM_PROVIDER_BADGE_COLOR);
        setStream(Boolean(provider.stream));
        setNotes(provider.notes || '');
        setModelDrafts(Object.fromEntries(
            (provider.models || []).map((model) => [
                model.value,
                createModelDraft(model.value, model),
            ])
        ));
    }, [provider]);

    useEffect(() => {
        setModelDrafts((current) => {
            const next = { ...current };
            let changed = false;
            for (const modelId of modelIds) {
                if (!next[modelId]) {
                    const sourceModel = (provider.models || []).find((item) => item.value === modelId);
                    next[modelId] = createModelDraft(modelId, sourceModel);
                    changed = true;
                }
            }
            for (const modelId of Object.keys(next)) {
                if (!modelIds.includes(modelId)) {
                    delete next[modelId];
                    changed = true;
                }
            }
            return changed ? next : current;
        });
    }, [modelIds, provider.models, provider.type]);

    const handleSave = useCallback(async () => {
        if (saving) return;
        setSaving(true);
        try {
            const models = modelIds.map((modelId) => buildModelConfig(modelId, modelDrafts[modelId] || createModelDraft(modelId)));
            await saveRuntimeProvider(provider.id, {
                enabled,
                ...(canEditIdentity ? { label: label.trim() || provider.label } : {}),
                ...(canEditConnection ? {
                    baseUrl: baseUrl.trim(),
                    apiKey: apiKey.trim(),
                } : {}),
                ...(canEditCustomSchema ? {
                    ...(isOpenAICompatible ? { protocol } : {}),
                    badgeColor,
                    models,
                    ...(isOpenAICompatible ? { stream } : {}),
                } : {}),
                notes: notes.trim(),
            });
            notifyProvidersUpdated();
            await onReload();
        } finally {
            setSaving(false);
        }
    }, [apiKey, badgeColor, baseUrl, canEditConnection, canEditCustomSchema, canEditIdentity, enabled, isOpenAICompatible, label, modelDrafts, modelIds, notes, onReload, protocol, provider.id, provider.label, provider.type, saving, stream]);

    const handleDelete = useCallback(async () => {
        if (!canDelete || deleting) return;
        setDeleting(true);
        try {
            await deleteRuntimeProvider(provider.id);
            notifyProvidersUpdated();
            await onReload();
            navigate('/settings/providers', { replace: true });
        } finally {
            setDeleting(false);
        }
    }, [canDelete, deleting, navigate, onReload, provider.id]);

    return (
        <div>
            <SectionHeader
                title={provider.label}
                subtitle={provider.type}
                actions={
                    <>
                        <SourceBadge source={provider.source} />
                        {canDelete && (
                            <button
                                type="button"
                                onClick={() => void handleDelete()}
                                disabled={deleting}
                                className="inline-flex items-center gap-2 rounded-lg border border-red-500/30 px-3 py-2 text-sm text-red-300 transition-colors hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                                删除
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={handleSave}
                            disabled={saving}
                            className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                            保存
                        </button>
                    </>
                }
            />
            <Panel>
                <div className="grid gap-4 xl:grid-cols-2">
                    <label className="block">
                        <FieldLabel locked={!canEditIdentity}>显示名称</FieldLabel>
                        <input
                            value={label}
                            onChange={(event) => setLabel(event.target.value)}
                            readOnly={!canEditIdentity}
                            className={fieldClassName(!canEditIdentity)}
                        />
                    </label>
                    <label className="block">
                        <FieldLabel locked>类型</FieldLabel>
                        <input
                            value={provider.type}
                            readOnly
                            className={fieldClassName(true)}
                        />
                    </label>
                    {isOpenAICompatible && (
                        <label className="block">
                            <FieldLabel locked={!canEditCustomSchema}>协议</FieldLabel>
                            <select
                                value={protocol}
                                onChange={(event) => setProtocol(event.target.value)}
                                disabled={!canEditCustomSchema}
                                className={fieldClassName(!canEditCustomSchema)}
                            >
                                {OPENAI_PROTOCOL_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                            </select>
                        </label>
                    )}
                    {!isSousaku && (
                        <>
                            <label className="block xl:col-span-2">
                                <FieldLabel>Base URL</FieldLabel>
                                <input
                                    value={baseUrl}
                                    onChange={(event) => setBaseUrl(event.target.value)}
                                    className={fieldClassName(false, 'font-mono')}
                                />
                            </label>
                            <label className="block">
                                <FieldLabel>API Key</FieldLabel>
                                <input
                                    value={apiKey}
                                    onChange={(event) => setApiKey(event.target.value)}
                                    placeholder="未配置"
                                    className={fieldClassName(false, 'font-mono')}
                                />
                            </label>
                            <label className="block xl:col-span-2">
                                <FieldLabel locked={!canEditCustomSchema}>添加模型</FieldLabel>
                                <textarea
                                    value={modelsText}
                                    onChange={(event) => setModelsText(event.target.value)}
                                    rows={3}
                                    readOnly={!canEditCustomSchema}
                                    placeholder="gpt-image-2, model-a; model-b&#10;支持逗号、分号或换行分隔"
                                    className={fieldClassName(!canEditCustomSchema, 'resize-none font-mono')}
                                />
                            </label>
                        </>
                    )}
                    {isOpenAICompatible && canEditCustomSchema && (
                        <>
                            <div className="block">
                                <ColorPreference
                                    label="标签配色"
                                    value={badgeColor}
                                    onChange={setBadgeColor}
                                    defaultValue={CUSTOM_PROVIDER_BADGE_COLOR}
                                />
                            </div>
                        </>
                    )}
                    {isOpenAICompatible && canEditCustomSchema && protocol === 'chat-completions' && (
                        <div className="xl:col-span-2">
                            <Toggle checked={stream} onChange={setStream} label="使用流式响应" />
                        </div>
                    )}
                    <div className="xl:col-span-2">
                        <Toggle checked={enabled} onChange={setEnabled} label="启用 Provider" />
                    </div>
                </div>
                {isOpenAICompatible && canEditCustomSchema && modelIds.length > 0 && (
                    <div className="mt-5 space-y-4">
                        <div>
                            <div className="text-sm font-medium text-[var(--text-primary)]">模型列表</div>
                            <div className="text-xs text-[var(--text-muted)]">点击模型展开，配置要显示的控件和请求字段。默认模型会自动取列表第一项。</div>
                        </div>
                        <div className="space-y-4">
                            {modelIds.map((modelId) => (
                                <ModelExposureEditor
                                    key={modelId}
                                    modelId={modelId}
                                    draft={modelDrafts[modelId] || createModelDraft(modelId)}
                                    onChange={(next) => setModelDrafts((current) => ({ ...current, [modelId]: next }))}
                                    onRemove={() => setModelsText((value) => modelListFromText(value).filter((item) => item !== modelId).join('\n'))}
                                />
                            ))}
                        </div>
                    </div>
                )}
                <label className="mt-5 block xl:col-span-2">
                    <FieldLabel>备注</FieldLabel>
                    <textarea
                        value={notes}
                        onChange={(event) => setNotes(event.target.value)}
                        rows={3}
                        className={fieldClassName(false, 'resize-none')}
                    />
                </label>
                <div className="mt-5">
                    <div className="mb-2 text-xs text-[var(--text-muted)]">能力</div>
                    <div className="flex flex-wrap gap-2">
                        {provider.capabilities.map((capability) => (
                            <span
                                key={capability}
                                className="rounded-full border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2.5 py-1 text-xs text-[var(--text-secondary)]"
                            >
                                {capability}
                            </span>
                        ))}
                    </div>
                </div>
            </Panel>
        </div>
    );
}

function JobsPanel({ settings }: { settings?: BackendSettings }) {
    const limits = settings?.jobs.providerLimits.value || {};
    return (
        <div>
            <SectionHeader title="任务" subtitle="Worker、并发和轮询参数" />
            <Panel>
                <SettingRow label="Worker" value={String(settings?.jobs.maxWorkers.value ?? '-')} source={settings?.jobs.maxWorkers.source} />
                <SettingRow label="轮询间隔" value={`${settings?.jobs.pollIntervalSeconds.value ?? '-'} 秒`} source={settings?.jobs.pollIntervalSeconds.source} />
                <SettingRow
                    label="默认超时"
                    value={`${Math.round((settings?.jobs.defaultTimeoutSeconds.value || 0) / 60)} 分钟`}
                    source={settings?.jobs.defaultTimeoutSeconds.source}
                />
                <SettingRow
                    label="Sousaku 卡死判定"
                    value={`${Math.round((settings?.jobs.sousakuStaleTaskSeconds.value || 0) / 60)} 分钟`}
                    source={settings?.jobs.sousakuStaleTaskSeconds.source}
                />
                <div className="py-3">
                    <div className="mb-3 text-sm font-medium text-[var(--text-secondary)]">Provider 并发</div>
                    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)]">
                        {Object.entries(limits).map(([provider, limit]) => (
                            <div key={provider} className="grid grid-cols-[1fr_6rem] border-b border-[var(--border-subtle)] px-3 py-2 text-sm last:border-b-0">
                                <span className="text-[var(--text-secondary)]">{provider}</span>
                                <span className="text-right font-mono text-[var(--text-primary)]">{limit}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </Panel>
        </div>
    );
}

function StoragePanel({ settings, onReload }: { settings?: BackendSettings; onReload: () => Promise<void> }) {
    const [saveDir, setSaveDir] = useState('');
    const [thumbnailWidth, setThumbnailWidth] = useState(512);
    const [thumbnailQuality, setThumbnailQuality] = useState(78);
    const [thumbnailCacheMaxGb, setThumbnailCacheMaxGb] = useState(3);
    const [saving, setSaving] = useState(false);
    const [usage, setUsage] = useState<StorageUsage | null>(null);
    const [usageLoading, setUsageLoading] = useState(false);
    const [clearingCache, setClearingCache] = useState<'thumbnails' | null>(null);
    const reloadGalleryFromServer = useStore((state) => state.reloadGalleryFromServer);

    useEffect(() => {
        setSaveDir(settings?.paths.saveDir.value || '');
        setThumbnailWidth(settings?.gallery.thumbnailWidth.value || 512);
        setThumbnailQuality(settings?.gallery.thumbnailQuality.value || 78);
        setThumbnailCacheMaxGb(settings?.gallery.thumbnailCacheMaxGb.value || 3);
    }, [settings]);

    const refreshUsage = useCallback(async () => {
        setUsageLoading(true);
        try {
            setUsage(await loadStorageUsage());
        } catch (error) {
            console.error('Failed to load storage usage:', error);
        } finally {
            setUsageLoading(false);
        }
    }, []);

    useEffect(() => {
        refreshUsage();
    }, [refreshUsage, settings?.paths.saveDir.resolved]);

    const handleSave = useCallback(async () => {
        if (saving) return;
        setSaving(true);
        try {
            await saveBackendSettings({
                storage: {
                    saveDir: saveDir.trim(),
                },
                gallery: {
                    thumbnailWidth,
                    thumbnailQuality,
                    thumbnailCacheMaxGb,
                },
            });
            await onReload();
            await reloadGalleryFromServer();
            await refreshUsage();
        } finally {
            setSaving(false);
        }
    }, [onReload, refreshUsage, reloadGalleryFromServer, saveDir, saving, thumbnailCacheMaxGb, thumbnailQuality, thumbnailWidth]);

    const handleClearCache = useCallback(async (cacheName: 'thumbnails') => {
        if (clearingCache) return;
        setClearingCache(cacheName);
        try {
            await clearStorageCache(cacheName);
            await refreshUsage();
        } catch (error) {
            console.error('Failed to clear cache:', error);
            window.alert(error instanceof Error ? error.message : '清理缓存失败');
        } finally {
            setClearingCache(null);
        }
    }, [clearingCache, refreshUsage]);

    return (
        <div>
            <SectionHeader
                title="存储"
                subtitle="图片目录、数据库和缩略图缓存"
                actions={
                    <button
                        type="button"
                        onClick={handleSave}
                        disabled={saving}
                        className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        保存
                    </button>
                }
            />
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)]">
                <Panel>
                    <div className="space-y-4">
                        <label className="block">
                            <span className="mb-2 block text-xs text-[var(--text-muted)]">图片保存目录</span>
                            <input
                                value={saveDir}
                                onChange={(event) => setSaveDir(event.target.value)}
                                className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                            />
                            <span className="mt-1 block truncate text-xs text-[var(--text-muted)]">
                                当前解析路径：{settings?.paths.saveDir.resolved || '-'}
                            </span>
                        </label>
                        <div className="grid gap-3 md:grid-cols-3">
                            <label className="block">
                                <span className="mb-2 block text-xs text-[var(--text-muted)]">缩略图宽度</span>
                                <input
                                    type="number"
                                    min={128}
                                    max={2048}
                                    step={64}
                                    value={thumbnailWidth}
                                    onChange={(event) => setThumbnailWidth(Number(event.target.value))}
                                    className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                                />
                            </label>
                            <label className="block">
                                <span className="mb-2 block text-xs text-[var(--text-muted)]">缩略图质量</span>
                                <input
                                    type="number"
                                    min={30}
                                    max={95}
                                    value={thumbnailQuality}
                                    onChange={(event) => setThumbnailQuality(Number(event.target.value))}
                                    className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                                />
                            </label>
                            <label className="block">
                                <span className="mb-2 block text-xs text-[var(--text-muted)]">缓存上限 GB</span>
                                <input
                                    type="number"
                                    min={1}
                                    max={100}
                                    value={thumbnailCacheMaxGb}
                                    onChange={(event) => setThumbnailCacheMaxGb(Number(event.target.value))}
                                    className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                                />
                            </label>
                        </div>
                    </div>
                </Panel>
                <Panel>
                    <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                        <Database className="h-4 w-4 text-[var(--accent-primary)]" />
                        数据库
                    </div>
                    <SettingRow label="任务数据库" value={<PathValue value={settings?.paths.jobsDb} />} source={settings?.paths.jobsDb.source} />
                    <SettingRow label="图廊数据库" value={<PathValue value={settings?.paths.galleryDb} />} source={settings?.paths.galleryDb.source} />
                </Panel>
            </div>
            <div className="mt-4">
                <Panel>
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                            <HardDrive className="h-4 w-4 text-[var(--accent-primary)]" />
                            存储空间管理
                        </div>
                        <button
                            type="button"
                            onClick={refreshUsage}
                            disabled={usageLoading}
                            className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            <RotateCw className={`h-3.5 w-3.5 ${usageLoading ? 'animate-spin' : ''}`} />
                            刷新
                        </button>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-3">
                        <StorageUsageCard
                            title="画廊文件"
                            value={formatBytes(usage?.gallery.bytes)}
                            meta={`${usage?.gallery.files ?? 0} 个文件 / ${usage?.gallery.records ?? 0} 条记录`}
                            details={[
                                `其中本地导入：${usage?.gallery.imports.files ?? 0} 张，${formatBytes(usage?.gallery.imports.bytes)}`,
                                `缺失文件：${usage?.gallery.missing ?? 0}`,
                            ]}
                        />
                        <StorageUsageCard
                            title="缩略图缓存"
                            value={formatBytes(usage?.thumbnailCache.bytes)}
                            meta={`${usage?.thumbnailCache.files ?? 0} 个文件 / 上限 ${formatBytes(usage?.thumbnailCache.maxBytes)}`}
                            path={usage?.thumbnailCache.path}
                            actionLabel="释放"
                            actionLoading={clearingCache === 'thumbnails'}
                            actionDisabled={!usage?.thumbnailCache.bytes}
                            onAction={() => handleClearCache('thumbnails')}
                        />
                        <StorageUsageCard
                            title="参考图库"
                            value={formatBytes(usage?.referenceLibrary?.bytes)}
                            meta={`${usage?.referenceLibrary?.assets.files ?? 0} 张原图 / ${usage?.referenceLibrary?.thumbnails.files ?? 0} 个预览`}
                            path={usage?.referenceLibrary?.path}
                            details={[
                                `原图：${formatBytes(usage?.referenceLibrary?.assets.bytes)}`,
                                `预览：${formatBytes(usage?.referenceLibrary?.thumbnails.bytes)}`,
                            ]}
                        />
                    </div>
                </Panel>
            </div>
        </div>
    );
}

function StorageUsageCard({
    title,
    value,
    meta,
    path,
    details = [],
    actionLabel,
    actionLoading,
    actionDisabled,
    onAction,
}: {
    title: string;
    value: string;
    meta: string;
    path?: string;
    details?: string[];
    actionLabel?: string;
    actionLoading?: boolean;
    actionDisabled?: boolean;
    onAction?: () => void;
}) {
    return (
        <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-4">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-sm font-medium text-[var(--text-secondary)]">{title}</div>
                    <div className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{value}</div>
                    <div className="mt-1 text-xs text-[var(--text-muted)]">{meta}</div>
                </div>
                {actionLabel && onAction && (
                    <button
                        type="button"
                        onClick={onAction}
                        disabled={actionDisabled || actionLoading}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-2.5 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:border-rose-400/40 hover:bg-rose-500/10 hover:text-rose-200 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                        {actionLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                        {actionLabel}
                    </button>
                )}
            </div>
            {(path || details.length > 0) && (
                <div className="mt-3 space-y-1 text-xs text-[var(--text-muted)]">
                    {path && <div className="truncate font-mono">{path}</div>}
                    {details.map((detail) => (
                        <div key={detail}>{detail}</div>
                    ))}
                </div>
            )}
        </div>
    );
}

function NetworkPanel({ settings }: { settings?: BackendSettings }) {
    const proxies = settings?.network.httpProxies.value;
    return (
        <div>
            <SectionHeader title="网络" subtitle="代理和连接参数" />
            <Panel>
                <SettingRow label="HTTP 代理" value={proxies?.http || '未启用'} source={settings?.network.httpProxies.source} />
                <SettingRow label="HTTPS 代理" value={proxies?.https || '未启用'} source={settings?.network.httpProxies.source} />
                <SettingRow
                    label="公网 URL 有效期"
                    value={`${Math.round((settings?.network.publicUrlTtlSeconds.value || 0) / 60)} 分钟`}
                    source={settings?.network.publicUrlTtlSeconds.source}
                />
            </Panel>
        </div>
    );
}

function AdvancedPanel({ settings }: { settings?: BackendSettings }) {
    const configFiles = settings?.configFiles || {};
    return (
        <div>
            <SectionHeader title="高级" subtitle="配置来源、端口和诊断信息" />
            <div className="grid gap-4 xl:grid-cols-2">
                <Panel>
                    <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                        <Shield className="h-4 w-4 text-[var(--accent-primary)]" />
                        服务
                    </div>
                    <SettingRow label="后端端口" value={String(settings?.server.backendPort.value ?? '-')} source={settings?.server.backendPort.source} />
                    <SettingRow label="前端端口" value={String(settings?.server.frontendPort.value ?? '-')} source={settings?.server.frontendPort.source} />
                    <SettingRow label="自动重载" value={settings?.server.useReloader.value ? '开启' : '关闭'} source={settings?.server.useReloader.source} />
                    <SettingRow label="日志等级" value={settings?.logging.level.value ?? '-'} source={settings?.logging.level.source} />
                    <SettingRow label="日志颜色" value={settings?.logging.color.value ? '开启' : '关闭'} source={settings?.logging.color.source} />
                    <SettingRow label="Sousaku 进度面板" value={settings?.logging.sousakuProgressPanel.value ? '开启' : '关闭'} source={settings?.logging.sousakuProgressPanel.source} />
                </Panel>
                <Panel>
                    <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                        <Database className="h-4 w-4 text-[var(--accent-primary)]" />
                        配置文件
                    </div>
                    {Object.entries(configFiles).map(([key, file]) => (
                        <SettingRow
                            key={key}
                            label={key}
                            value={
                                <div className="flex min-w-0 items-center gap-2">
                                    {file.exists ? <Check className="h-4 w-4 shrink-0 text-emerald-400" /> : <span className="h-4 w-4 shrink-0 rounded-full border border-[var(--border-subtle)]" />}
                                    <span className="min-w-0 truncate font-mono text-xs">{file.path}</span>
                                </div>
                            }
                            source={file.exists ? '文件存在' : '未创建'}
                        />
                    ))}
                </Panel>
            </div>
        </div>
    );
}

function LoadingPanel() {
    return (
        <div className="flex min-h-[24rem] items-center justify-center text-[var(--text-muted)]">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            加载设置
        </div>
    );
}

export function SettingsPage() {
    const params = useParams();
    const navigate = useNavigate();
    const section = (params.category || 'preferences') as SectionId;
    const [settings, setSettings] = useState<BackendSettings>();
    const [providers, setProviders] = useState<RuntimeProvider[]>([]);
    const [loading, setLoading] = useState(true);
    const [resetting, setResetting] = useState(false);
    const resetUiSettingsToDefaults = useStore((state) => state.resetUiSettingsToDefaults);

    const loadSettings = useCallback(async (cancelled?: () => boolean) => {
        setLoading(true);
        try {
            const [settingsData, providerData] = await Promise.all([
                loadBackendSettings(),
                loadRuntimeProviders(),
            ]);
            if (!cancelled?.()) {
                setSettings(settingsData);
                setProviders(providerData);
            }
        } finally {
            if (!cancelled?.()) setLoading(false);
        }
    }, []);

    useEffect(() => {
        let cancelled = false;
        loadSettings(() => cancelled).catch(() => {
            if (!cancelled) setLoading(false);
        });
        return () => {
            cancelled = true;
        };
    }, [loadSettings]);

    useEffect(() => {
        const valid = SECTIONS.some((item) => item.id === section);
        if (!valid) {
            navigate('/settings/preferences', { replace: true });
            return;
        }
        if (section === 'providers' && providers.length > 0 && !params.item) {
            navigate(`/settings/providers/${providers[0].id}`, { replace: true });
        }
    }, [navigate, params.item, providers, section]);

    const activeSection = useMemo(
        () => SECTIONS.find((item) => item.id === section) || SECTIONS[0],
        [section],
    );

    const handleResetDefaults = useCallback(async () => {
        if (resetting) return;
        const confirmed = window.confirm('恢复默认配置会重置全部设置，包括图片保存目录、并发、网络和偏好设置。确定继续吗？');
        if (!confirmed) return;
        setResetting(true);
        try {
            await resetUiSettingsToDefaults();
            await loadSettings();
        } finally {
            setResetting(false);
        }
    }, [loadSettings, resetUiSettingsToDefaults, resetting]);

    return (
        <main className="mx-auto flex w-full max-w-[1800px] flex-1 flex-col gap-4 px-4 py-4">
            <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-4">
                <div>
                    <div className="text-lg font-semibold text-[var(--text-primary)]">设置</div>
                    <div className="text-xs text-[var(--text-muted)]">本地工作台、Provider 和运行参数</div>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        disabled={resetting}
                        onClick={handleResetDefaults}
                        className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {resetting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
                        恢复默认配置
                    </button>
                </div>
            </section>

            <div className="grid min-h-[calc(100vh-8rem)] gap-4 lg:grid-cols-[13rem_1fr]">
                <aside className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-2">
                    <nav className="space-y-1">
                        {SECTIONS.map((item) => {
                            const Icon = item.icon;
                            return (
                                <NavLink
                                    key={item.id}
                                    to={`/settings/${item.id}`}
                                    className={({ isActive }) =>
                                        [
                                            'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',
                                            isActive || activeSection.id === item.id
                                                ? 'bg-[var(--accent-primary)]/18 text-[var(--text-primary)]'
                                                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]',
                                        ].join(' ')
                                    }
                                >
                                    <Icon className="h-4 w-4" />
                                    {item.label}
                                </NavLink>
                            );
                        })}
                    </nav>
                </aside>

                <section className="min-w-0">
                    {loading ? (
                        <LoadingPanel />
                    ) : section === 'preferences' ? (
                        <PreferencesPanel />
                    ) : section === 'providers' ? (
                        <ProviderPanel providers={providers} activeProviderId={params.item} onReload={() => loadSettings()} />
                    ) : section === 'jobs' ? (
                        <JobsPanel settings={settings} />
                    ) : section === 'storage' ? (
                        <StoragePanel settings={settings} onReload={() => loadSettings()} />
                    ) : section === 'network' ? (
                        <NetworkPanel settings={settings} />
                    ) : (
                        <AdvancedPanel settings={settings} />
                    )}
                </section>
            </div>
        </main>
    );
}
