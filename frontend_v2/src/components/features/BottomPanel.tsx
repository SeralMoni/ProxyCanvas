import { useState, useRef, useMemo, useEffect } from 'react';
import { Send, Image as ImageIcon, Settings, Loader2, X, Plus, Check, Paintbrush } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useStore, useGenerateParams } from '../../store';
import type { RuntimeProvider } from '../../services/api';
import { startGeneration } from '../../services/generationService';
import type { GenerateParams, ImageItem, ReferenceImageInput, UploadedImage } from '../../types';
import { isMaskSupported } from '../../types';
import { MaskEditor } from './MaskEditor';
import { useProviders } from '../../hooks/useProviders';
import { generationProviderOptions } from '../../utils/providers';

type ModelOption = {
    value: string;
    label?: string;
    defaults?: Partial<GenerateParams>;
    controls?: ModelControl[];
    constraints?: ModelConstraints;
    features?: Record<string, unknown>;
    payload?: Record<string, unknown>;
};

type ControlOption = string | number | boolean | { value: string | number | boolean; label?: string };
type ModelControl = {
    key: keyof GenerateParams;
    label?: string;
    type?: 'select' | 'boolean' | 'number';
    options?: ControlOption[];
    min?: number;
    max?: number;
    step?: number;
};
type ModelConstraints = {
    fixedImageCount?: number;
    resolutionByRatio?: Record<string, string[]>;
};

const HIDDEN_MODEL_CONTROL_KEYS = new Set(['referenceImage', 'mask']);

const FALLBACK_CLIPROXY_MODELS: ModelOption[] = [
    { value: 'gpt-image-2', label: 'GPT-Image-2' },
    { value: 'gemini-3.1-flash-image', label: 'Gemini 3.1 Flash Image' },
];

const FALLBACK_SOUSAKU_MODELS: ModelOption[] = [
    { value: 'gpt-image-2-low', label: 'GPT Image 2.0 Low' },
    { value: 'gpt-image-2', label: 'GPT Image 2.0 Medium' },
    { value: 'gpt-image-2-high', label: 'GPT Image 2.0 High' },
    { value: 'wan-image-2.7-pro', label: 'WAN Image 2.7 Pro' },
    { value: 'mj-image-v7', label: 'Midjourney V7' },
    { value: 'mj-image-niji-7', label: 'Midjourney Niji 7' },
];

const FALLBACK_APIMART_MODELS: ModelOption[] = [
    { value: 'gemini-3-pro-image-preview', label: 'Gemini 3 Pro' },
    { value: 'gemini-3.1-flash-image-preview', label: 'Gemini 3.1 Flash' },
    { value: 'gpt-image-2', label: 'GPT-Image-2' },
    { value: 'gpt-image-2-official', label: 'GPT-Image-2 Official' },
];

function providerModelOptions(providers: RuntimeProvider[], providerId: string, fallback: ModelOption[]): ModelOption[] {
    const configured = providers.find((provider) => provider.id === providerId)?.models || [];
    const usable = configured
        .filter((model) => model.value)
        .map((model) => ({
            ...model,
            defaults: model.defaults as Partial<GenerateParams> | undefined,
            controls: model.controls as ModelControl[] | undefined,
            constraints: model.constraints as ModelConstraints | undefined,
            features: model.features,
            payload: model.payload,
        }));
    return usable.length > 0 ? usable : fallback;
}

function providerDefaultModel(providers: RuntimeProvider[], providerId: string, fallback: string) {
    return providers.find((provider) => provider.id === providerId)?.defaultModel || fallback;
}

function modelParamKey(api: string): keyof GenerateParams {
    if (api === 'apimart') return 'apimartModel';
    if (api === 'cliproxy') return 'cliproxyModel';
    if (api === 'sousaku') return 'sousakuModel';
    return 'model';
}

function optionValue(option: ControlOption) {
    return typeof option === 'object' ? option.value : option;
}

function optionLabel(option: ControlOption) {
    if (typeof option === 'object') return option.label || String(option.value);
    return String(option);
}

function parseControlValue(value: string, sample: ControlOption | undefined, control: ModelControl) {
    const raw = sample ? optionValue(sample) : value;
    if (typeof raw === 'number' || control.key === 'imageCount') return Number(value);
    if (typeof raw === 'boolean' || control.type === 'boolean') return value === 'true';
    return value;
}

function pixelSizeFor(model: ModelOption | undefined, ratio: string, resolution: string) {
    const pixelMap = model?.payload?.pixelSizeMap;
    if (!pixelMap || typeof pixelMap !== 'object') return undefined;
    const byRatio = (pixelMap as Record<string, Record<string, string | null>>)[ratio];
    return byRatio?.[resolution] || undefined;
}

function compactReferenceInput(img: UploadedImage): ReferenceImageInput | null {
    const refId = img.refId;
    const localUrl = img.localUrl || (refId ? `/api/reference-images/${refId}` : undefined);
    const fallbackUrl = img.base64 && !img.base64.startsWith('data:')
        ? img.base64
        : undefined;
    const publicUrl = img.publicUrl && !img.publicUrl.startsWith('data:')
        ? img.publicUrl
        : undefined;
    const url = localUrl || fallbackUrl;
    const item: ReferenceImageInput = {};

    if (url) item.url = url;
    if (refId) item.ref_id = refId;
    if (publicUrl) item.public_url = publicUrl;
    if (img.name) item.name = img.name;

    return item.ref_id || item.url || item.public_url ? item : null;
}

function isReferenceReady(img: UploadedImage) {
    return Boolean(img.refId || img.localUrl || img.publicUrl || img.base64) && (img.status || 'ready') === 'ready';
}

export function BottomPanel() {
    const selectedApi = useStore((s) => s.selectedApi);
    const setSelectedApi = useStore((s) => s.setSelectedApi);
    const selectedModelByApi = useStore((s) => s.selectedModelByApi);
    const setSelectedModel = useStore((s) => s.setSelectedModel);
    const generateParams = useGenerateParams();
    const setGenerateParams = useStore((s) => s.setGenerateParams);
    const uploadedImages = useStore((s) => s.uploadedImages);
    const selectedRefs = useStore((s) => s.selectedReferenceIds);
    const loadReferenceImagesFromServer = useStore((s) => s.loadReferenceImagesFromServer);
    const uploadReferenceImages = useStore((s) => s.uploadReferenceImages);
    const addReferenceUrl = useStore((s) => s.addReferenceUrl);
    const removeUploadedImage = useStore((s) => s.removeUploadedImage);
    const clearSelectedReferenceIds = useStore((s) => s.clearSelectedReferenceIds);
    const toggleSelectedReferenceId = useStore((s) => s.toggleSelectedReferenceId);
    const currentPrompt = useStore((s) => s.currentPrompt);
    const setCurrentPrompt = useStore((s) => s.setCurrentPrompt);
    const autoClearPrompt = useStore((s) => s.autoClearPrompt);
    const addImagesLocal = useStore((s) => s.addImagesLocal);
    const updateImage = useStore((s) => s.updateImage);
    const removeImageLocalOnly = useStore((s) => s.removeImageLocalOnly);
    const addThoughtImages = useStore((s) => s.addThoughtImages);
    const maskData = useStore((s) => s.maskData);
    const maskFeather = useStore((s) => s.maskFeather);
    const setMaskData = useStore((s) => s.setMaskData);
    const removeMaskData = useStore((s) => s.removeMaskData);

    const [showSettings, setShowSettings] = useState(false);
    const [maskEditingImageId, setMaskEditingImageId] = useState<string | null>(null);
    const [showImagePicker, setShowImagePicker] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isPromptFocused, setIsPromptFocused] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [referenceTotal, setReferenceTotal] = useState(0);
    const [referenceLoadingMore, setReferenceLoadingMore] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const referenceLibraryLoadedRef = useRef(false);
    const referenceRecoveryAttemptedRef = useRef(false);
    const { providers } = useProviders();

    const apiOptions = useMemo(() => {
        return generationProviderOptions(providers);
    }, [providers]);
    const cliproxyModelOptions = useMemo(
        () => providerModelOptions(providers, 'cliproxy', FALLBACK_CLIPROXY_MODELS),
        [providers],
    );
    const sousakuModelOptions = useMemo(
        () => providerModelOptions(providers, 'sousaku', FALLBACK_SOUSAKU_MODELS),
        [providers],
    );
    const apimartModelOptions = useMemo(
        () => providerModelOptions(providers, 'apimart', FALLBACK_APIMART_MODELS),
        [providers],
    );
    const selectedProvider = useMemo(
        () => providers.find((provider) => provider.id === selectedApi),
        [providers, selectedApi],
    );

    useEffect(() => {
        if (!apiOptions.some((option) => option.value === selectedApi)) {
            setSelectedApi(apiOptions[0].value);
        }
    }, [apiOptions, selectedApi, setSelectedApi]);

    useEffect(() => {
        if (!showImagePicker) {
            referenceRecoveryAttemptedRef.current = false;
            return;
        }
        const missingSelected = selectedRefs.some((id) => !uploadedImages.some((img) => img.id === id));
        const shouldRecoverSelection = missingSelected && !referenceRecoveryAttemptedRef.current;
        if (referenceLibraryLoadedRef.current && !shouldRecoverSelection) return;
        if (shouldRecoverSelection) {
            referenceRecoveryAttemptedRef.current = true;
        }
        referenceLibraryLoadedRef.current = true;
        loadReferenceImagesFromServer({ reset: true }).then((result) => {
            setReferenceTotal(result.total);
        }).catch(() => {
            referenceLibraryLoadedRef.current = false;
        });
    }, [loadReferenceImagesFromServer, selectedRefs, showImagePicker, uploadedImages]);

    useEffect(() => {
        if (!showImagePicker) return;
        const validIds = new Set(uploadedImages.filter(isReferenceReady).map((img) => img.id));
        const validSelectedRefs = selectedRefs.filter((id) => validIds.has(id));
        if (validSelectedRefs.length !== selectedRefs.length) {
            useStore.getState().setSelectedReferenceIds(validSelectedRefs);
        }
    }, [selectedRefs, showImagePicker, uploadedImages]);

    const handleLoadMoreReferences = async () => {
        if (referenceLoadingMore) return;
        setReferenceLoadingMore(true);
        try {
            const result = await loadReferenceImagesFromServer();
            setReferenceTotal(result.total);
        } finally {
            setReferenceLoadingMore(false);
        }
    };

    const handleRemoveReferenceImage = async (img: UploadedImage) => {
        try {
            await removeUploadedImage(img.id);
            if (img.refId) {
                setReferenceTotal((total) => Math.max(0, total - 1));
            }
        } catch (removeError) {
            setError(removeError instanceof Error ? removeError.message : '参考图删除失败');
        }
    };

    // Upload file to image hosting and get URL
    const handleFileUpload = async (files: FileList | null) => {
        if (!files) return;

        const fileArray = Array.from(files).filter(f => f.type.startsWith('image/'));
        if (fileArray.length === 0) return;

        uploadReferenceImages(fileArray);
    };

    // Handle URL drop - directly use URL without downloading (avoids CORS issues)
    const handleUrlUpload = async (url: string) => {
        try {
            addReferenceUrl(url);
        } catch (err) {
            setError(`URL 处理失败: ${err instanceof Error ? err.message : '未知错误'}`);
        }
    };

    // Drag and drop event handlers
    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
    };

    const handleDrop = async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        // Priority 1: Handle file drops
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            await handleFileUpload(e.dataTransfer.files);
            return;
        }

        // Priority 2: Handle URL drops
        const url = e.dataTransfer.getData('text/plain');
        if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
            // Check if it's likely an image URL
            const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'];
            const isImageUrl = imageExtensions.some(ext => url.toLowerCase().includes(ext));

            if (isImageUrl || url.includes('image')) {
                await handleUrlUpload(url);
            } else {
                setError('请拖拽图片 URL（需包含图片格式后缀）');
            }
        }
    };

    const toggleRefSelection = (id: string) => {
        toggleSelectedReferenceId(id);
    };

    const handleGenerate = async () => {
        if (!currentPrompt.trim()) return;

        setError(null);

        // Get selected reference images - use their URLs, preserving SELECTION order
        const selectedImages = selectedRefs
            .map(id => uploadedImages.find(img => img.id === id))
            .filter((img): img is UploadedImage => !!img && isReferenceReady(img));
        if (selectedRefs.length > selectedImages.length) {
            setError('有参考图尚未就绪，请重新确认选择。');
            return;
        }
        const imageUrls = selectedImages
            .map(compactReferenceInput)
            .filter((item): item is ReferenceImageInput => Boolean(item));

        const prompt = currentPrompt;
        const params = { ...generateParams };
        const fixedImageCount = Number(selectedModelConfig?.constraints?.fixedImageCount || 0);
        const imageCount = fixedImageCount || Number(generateParams.imageCount || selectedModelConfig?.defaults?.imageCount || 1);
        if (fixedImageCount) {
            params.imageCount = fixedImageCount;
        }
        if (selectedApi === 'openai') {
            params.ratio = generateParams.ratio || '16:9';
            delete params.size;
            delete params.quality;
        }
        const apiType = selectedApi;

        // 1. Create placeholder images immediately
        const placeholderIds: string[] = [];
        const placeholders: ImageItem[] = [];
        const batchCreatedAtMs = Date.now();
        for (let i = 0; i < imageCount; i++) {
            const placeholderId = crypto.randomUUID();
            placeholderIds.push(placeholderId);

            placeholders.push({
                id: placeholderId,
                status: 'loading',
                localPath: '',
                thumbnail: '',
                prompt,
                apiType,
                params,
                createdAt: new Date(batchCreatedAtMs - i).toISOString(),
                isFavorite: false,
                tags: [],
                inputImages: imageUrls,
                resultIndex: i + 1,
            });
        }
        addImagesLocal(placeholders);

        // Collect mask data for selected images
        const firstMaskedId = selectedRefs.find(id => maskData[id]);
        const activeMaskData = firstMaskedId ? maskData[firstMaskedId] : undefined;
        const activeMaskFeather = firstMaskedId ? (maskFeather[firstMaskedId] ?? 0) : undefined;

        // 3. Fire generation in background (don't await)
        startGeneration(
            {
                prompt,
                apiType,
                params,
                modelConfig: selectedModelConfig,
                imageUrls: imageUrls.length > 0 ? imageUrls : undefined,
                placeholderIds,
                maskDataUrl: activeMaskData,
                maskFeather: activeMaskFeather,
            },
            {
                onSuccess: (placeholderId, result) => {
                    console.log(`✅ Image ${placeholderId} completed`);
                    return updateImage(placeholderId, result);
                },
                onError: (placeholderId, error) => {
                    console.error(`❌ Image ${placeholderId} failed:`, error);
                    setError(error);
                    removeImageLocalOnly(placeholderId);
                },
                onReset: (placeholderId) => {
                    updateImage(placeholderId, {
                        status: 'loading',
                        localPath: '',
                        thumbnail: '',
                        savedFilePath: undefined,
                        originalUrl: undefined,
                        tags: [],
                    });
                },
                onThoughtImages: (images) => {
                    console.log(`🎨 Received ${images.length} thought images`);
                    addThoughtImages(images);
                },
            }
        );
        if (autoClearPrompt) {
            setCurrentPrompt('');
        }
    };

    const ratioOptions = ['auto', '1:1', '1:2', '1:4', '1:8', '2:1', '2:3', '3:2', '3:4', '4:1', '4:3', '4:5', '5:4', '8:1', '9:16', '9:21', '16:9', '21:9'];
    const qualityOptions = [
        { value: 'standard', label: '1K' },
        { value: 'medium', label: '2K' },
        { value: 'hd', label: '4K' },
    ];
    const openaiModelOptions = useMemo(
        () => providerModelOptions(providers, 'openai', [{
            value: 'gpt-image-2',
            label: 'GPT-Image-2',
            defaults: { ratio: '16:9', imageCount: 1 },
            controls: [
                { key: 'ratio', label: '比例', type: 'select', options: ratioOptions },
                { key: 'imageCount', label: '数量', type: 'select', options: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] },
            ],
            features: { referenceImage: true, mask: true },
        }]),
        [providers],
    );
    const nanobanana2ModelOptions = useMemo(
        () => providerModelOptions(providers, 'nanobanana2', [{
            value: 'gemini-3.1-flash-image',
            label: 'Gemini 3.1 Flash Image',
            defaults: { ratio: '16:9', quality: 'hd', imageCount: 1, thinkingLevel: 'High' },
            controls: [
                { key: 'ratio', label: '比例', type: 'select', options: ratioOptions },
                { key: 'quality', label: '画质', type: 'select', options: qualityOptions },
                { key: 'imageCount', label: '数量', type: 'select', options: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] },
                { key: 'thinkingLevel', label: '思考', type: 'select', options: ['High', 'Minimal'] },
            ],
            features: { referenceImage: true, mask: true, thinking: true },
        }]),
        [providers],
    );
    const modelOptionsForSelectedApi = useMemo(() => {
        if (selectedApi === 'openai') return openaiModelOptions;
        if (selectedApi === 'cliproxy') return cliproxyModelOptions;
        if (selectedApi === 'sousaku') return sousakuModelOptions;
        if (selectedApi === 'nanobanana2') return nanobanana2ModelOptions;
        if (selectedApi === 'apimart') return apimartModelOptions;
        return providerModelOptions(providers, selectedApi, [{
            value: selectedProvider?.defaultModel || 'gpt-image-2',
            label: selectedProvider?.defaultModel || 'gpt-image-2',
            defaults: { ratio: '16:9', quality: 'high', imageCount: 1 },
            controls: [
                { key: 'ratio', label: '比例', type: 'select', options: ['1:1', '4:3', '3:4', '16:9', '9:16'] },
                { key: 'quality', label: '质量', type: 'select', options: ['low', 'medium', 'high'] },
                { key: 'imageCount', label: '数量', type: 'select', options: [1, 2, 3, 4] },
            ],
            features: { referenceImage: true, mask: true },
        }]);
    }, [apimartModelOptions, cliproxyModelOptions, nanobanana2ModelOptions, openaiModelOptions, providers, selectedApi, selectedProvider, sousakuModelOptions]);
    const selectedModelKey = modelParamKey(selectedApi);
    const selectedModelValue = String(
        selectedModelByApi[selectedApi] ||
        generateParams[selectedModelKey] ||
        providerDefaultModel(providers, selectedApi, modelOptionsForSelectedApi[0]?.value || '') ||
        modelOptionsForSelectedApi[0]?.value ||
        '',
    );
    const selectedModelConfig = modelOptionsForSelectedApi.find((model) => model.value === selectedModelValue) || modelOptionsForSelectedApi[0];
    const selectedModelControls = (selectedModelConfig?.controls || []).filter(
        (control) => !HIDDEN_MODEL_CONTROL_KEYS.has(String(control.key)),
    );

    const handleModelChange = (nextModelValue: string) => {
        setSelectedModel(nextModelValue);
    };

    const isControlOptionDisabled = (control: ModelControl, value: string) => {
        const resolutionByRatio = selectedModelConfig?.constraints?.resolutionByRatio;
        if (!resolutionByRatio) return false;
        if (control.key === 'resolution') {
            const ratio = String(generateParams.ratio || selectedModelConfig?.defaults?.ratio || '16:9');
            const allowedRatios = resolutionByRatio[value];
            return Boolean(allowedRatios && ratio !== 'auto' && !allowedRatios.includes(ratio));
        }
        if (control.key === 'ratio') {
            const resolution = String(generateParams.resolution || selectedModelConfig?.defaults?.resolution || '');
            const allowedRatios = resolutionByRatio[resolution];
            return Boolean(allowedRatios && value !== 'auto' && !allowedRatios.includes(value));
        }
        return false;
    };

    const handleControlChange = (control: ModelControl, rawValue: string, sample?: ControlOption) => {
        const nextValue = parseControlValue(rawValue, sample, control);
        const updates: Partial<GenerateParams> = { [control.key]: nextValue };
        const resolutionByRatio = selectedModelConfig?.constraints?.resolutionByRatio;
        if (control.key === 'ratio' && resolutionByRatio) {
            const currentResolution = String(generateParams.resolution || selectedModelConfig?.defaults?.resolution || '');
            const allowedRatios = resolutionByRatio[currentResolution];
            if (allowedRatios && rawValue !== 'auto' && !allowedRatios.includes(rawValue)) {
                updates.resolution = '2K';
            }
        }
        setGenerateParams(updates);
    };

    const renderControl = (control: ModelControl) => {
        const label = control.label || String(control.key);
        const fixedImageCount = Number(selectedModelConfig?.constraints?.fixedImageCount || 0);
        const controlValue = control.key === 'imageCount' && fixedImageCount
            ? fixedImageCount
            : generateParams[control.key] ?? selectedModelConfig?.defaults?.[control.key] ?? '';
        if (control.type === 'boolean') {
            return (
                <div key={String(control.key)} className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-muted)]">{label}:</span>
                    <select
                        value={controlValue ? 'true' : 'false'}
                        onChange={(e) => handleControlChange(control, e.target.value)}
                        className="px-2 py-1 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                    >
                        <option value="true">开启</option>
                        <option value="false">关闭</option>
                    </select>
                </div>
            );
        }
        if (control.type === 'number') {
            return (
                <label key={String(control.key)} className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-muted)]">{label}:</span>
                    <input
                        type="number"
                        min={control.min}
                        max={control.max}
                        step={control.step || 1}
                        value={String(controlValue)}
                        onChange={(e) => handleControlChange(control, e.target.value)}
                        className="w-20 px-2 py-1 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                    />
                </label>
            );
        }
        const options = control.options || [];
        return (
            <div key={String(control.key)} className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)]">{label}:</span>
                <select
                    value={String(controlValue)}
                    onChange={(e) => {
                        const sample = options.find((option) => String(optionValue(option)) === e.target.value);
                        handleControlChange(control, e.target.value, sample);
                    }}
                    className="px-2 py-1 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                >
                    {options.map((option) => {
                        const value = String(optionValue(option));
                        const disabled = isControlOptionDisabled(control, value);
                        const pixelSize = control.key === 'resolution'
                            ? pixelSizeFor(selectedModelConfig, String(generateParams.ratio || selectedModelConfig?.defaults?.ratio || '16:9'), value)
                            : undefined;
                        return (
                            <option key={value} value={value} disabled={disabled}>
                                {optionLabel(option)}{disabled ? ' (不可用)' : ''}{pixelSize ? ` (${pixelSize})` : ''}
                            </option>
                        );
                    })}
                </select>
            </div>
        );
    };

    const configuredControls = (
        <>
            {modelOptionsForSelectedApi.length > 1 && (
                <div className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-muted)]">模型:</span>
                    <select
                        value={selectedModelValue}
                        onChange={(e) => handleModelChange(e.target.value)}
                        className="px-2 py-1 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                    >
                        {modelOptionsForSelectedApi.map((model) => (
                            <option key={model.value} value={model.value}>{model.label || model.value}</option>
                        ))}
                    </select>
                </div>
            )}
            {selectedModelControls.map(renderControl)}
        </>
    );

    const uploadingCount = uploadedImages.filter((img) => img.status === 'uploading').length;
    const selectedCount = selectedRefs.length;

    // Check if current API+model supports mask editing
    const currentModel = selectedModelValue;
    const maskSupported = useMemo(
        () => typeof selectedModelConfig?.features?.mask === 'boolean'
            ? Boolean(selectedModelConfig.features.mask)
            : isMaskSupported(selectedApi, currentModel),
        [currentModel, selectedApi, selectedModelConfig]
    );

    return (
        <>
            {/* Floating centered input panel */}
            <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 w-full max-w-2xl px-4">
                {/* Error message */}
                <AnimatePresence>
                    {error && (
                        <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: 10 }}
                            className="mb-2 px-4 py-2 rounded-xl bg-red-500/20 text-red-400 text-sm flex items-center justify-between backdrop-blur-sm"
                        >
                            <span>{error}</span>
                            <button onClick={() => setError(null)}>
                                <X className="w-4 h-4" />
                            </button>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Settings panel (collapsible) */}
                <AnimatePresence>
                    {showSettings && (
                        <motion.div
                            initial={{ opacity: 0, y: 10, scale: 0.95 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: 10, scale: 0.95 }}
                            className="mb-2 p-3 rounded-xl glass shadow-lg"
                        >
                            <div className="flex flex-wrap items-center gap-3">
                                {/* API Selector */}
                                <div className="flex items-center gap-2">
                                    <span className="text-xs text-[var(--text-muted)]">API:</span>
                                    <select
                                        value={selectedApi}
                                        onChange={(e) => {
                                            setSelectedApi(e.target.value);
                                        }}
                                        className="px-2 py-1 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                                    >
                                        {apiOptions.map((option) => (
                                            <option key={option.value} value={option.value}>
                                                {option.label}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                {configuredControls}
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Main input bar */}
                <div className="flex items-center gap-2 p-2 rounded-2xl glass shadow-2xl border border-[var(--border-subtle)]">
                    {/* Image picker button */}
                    <button
                        onClick={() => setShowImagePicker(true)}
                        className={`relative p-2.5 rounded-xl transition-colors ${selectedCount > 0
                            ? 'bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]'
                            : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]'
                            }`}
                        title="选择参考图"
                    >
                        <ImageIcon className="w-5 h-5" />
                        {selectedCount > 0 && (
                            <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-[var(--accent-primary)] text-white text-xs flex items-center justify-center">
                                {selectedCount}
                            </span>
                        )}
                    </button>

                    {/* Settings toggle */}
                    <button
                        onClick={() => setShowSettings(!showSettings)}
                        className={`p-2.5 rounded-xl transition-colors ${showSettings
                            ? 'bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]'
                            : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]'
                            }`}
                        title="设置"
                    >
                        <Settings className="w-5 h-5" />
                    </button>

                    {/* Prompt input - expandable textarea */}
                    <motion.textarea
                        ref={textareaRef}
                        value={currentPrompt}
                        onChange={(e) => setCurrentPrompt(e.target.value)}
                        onFocus={() => setIsPromptFocused(true)}
                        onBlur={(e) => {
                            // Don't collapse if clicking on action buttons (send, image, settings)
                            const relatedTarget = e.relatedTarget as HTMLElement | null;
                            if (relatedTarget?.closest('button')) {
                                return; // Keep expanded when clicking buttons
                            }
                            setIsPromptFocused(false);
                        }}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handleGenerate();
                            }
                        }}
                        placeholder="输入你的创意描述..."
                        rows={1}
                        animate={{
                            height: isPromptFocused ? 120 : 40,
                        }}
                        transition={{ duration: 0.2, ease: 'easeOut' }}
                        className="flex-1 px-4 py-2.5 bg-transparent text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none resize-none overflow-y-auto"
                        style={{ lineHeight: '1.5' }}
                    />

                    {/* Generate button */}
                    <button
                        onClick={handleGenerate}
                        disabled={!currentPrompt.trim()}
                        className={`p-2.5 rounded-xl transition-all ${currentPrompt.trim()
                            ? 'bg-[var(--accent-primary)] hover:bg-[var(--accent-secondary)] hover-glow'
                            : 'bg-[var(--bg-secondary)] text-[var(--text-muted)] cursor-not-allowed'
                            } text-white`}
                    >
                        <Send className="w-5 h-5" />
                    </button>
                </div>
            </div>

            {/* Image Picker Modal */}
            <AnimatePresence>
                {showImagePicker && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                        onClick={() => setShowImagePicker(false)}
                    >
                        <motion.div
                            initial={{ scale: 0.9, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.9, opacity: 0 }}
                            className="w-full max-w-lg mx-4 p-4 rounded-2xl glass shadow-2xl"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="text-lg font-semibold text-[var(--text-primary)]">选择参考图</h3>
                                <button
                                    onClick={() => setShowImagePicker(false)}
                                    className="p-1 rounded-lg hover:bg-[var(--bg-card-hover)] text-[var(--text-secondary)]"
                                >
                                    <X className="w-5 h-5" />
                                </button>
                            </div>

                            {/* Upload area */}
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept="image/*"
                                multiple
                                className="hidden"
                                onChange={(e) => handleFileUpload(e.target.files)}
                            />
                            <button
                                onClick={() => fileInputRef.current?.click()}
                                disabled={uploadingCount > 0}
                                onDragOver={handleDragOver}
                                onDragLeave={handleDragLeave}
                                onDrop={handleDrop}
                                className={`w-full p-6 mb-4 rounded-xl border-2 border-dashed transition-colors flex flex-col items-center gap-2 disabled:opacity-50 ${isDragging
                                    ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]'
                                    : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)] text-[var(--text-secondary)] hover:text-[var(--accent-primary)]'
                                    }`}
                            >
                                {uploadingCount > 0 ? (
                                    <>
                                        <Loader2 className="w-8 h-8 animate-spin" />
                                        <span className="text-sm">正在上传 {uploadingCount} 张图片...</span>
                                    </>
                                ) : (
                                    <>
                                        <Plus className="w-8 h-8" />
                                        <span className="text-sm">点击或拖拽上传图片（支持 URL 链接）</span>
                                    </>
                                )}
                            </button>

                            {/* Uploaded images grid */}
                            {uploadedImages.length > 0 ? (
                                <div className="max-h-64 overflow-y-auto pr-1">
                                    <div className="grid grid-cols-4 gap-2">
                                        {uploadedImages.map((img) => {
                                            const isReady = isReferenceReady(img);
                                            const isUploading = img.status === 'uploading';
                                            return (
                                            <div
                                                key={img.id}
                                                onClick={() => isReady && toggleRefSelection(img.id)}
                                                className={`relative aspect-square rounded-lg overflow-hidden border-2 transition-all ${!isReady
                                                    ? 'border-yellow-500/50 opacity-50'
                                                    : selectedRefs.includes(img.id)
                                                        ? 'border-[var(--accent-primary)] ring-2 ring-[var(--accent-primary)]/50'
                                                        : 'border-transparent hover:border-[var(--text-muted)]'
                                                    } ${isReady ? 'cursor-pointer' : 'cursor-not-allowed'}`}
                                            >
                                                <img
                                                    src={img.preview}
                                                    alt="Uploaded"
                                                    className="w-full h-full object-cover"
                                                />
                                                {!isReady && (
                                                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/55 px-1 text-center">
                                                        {isUploading ? (
                                                            <>
                                                                <Loader2 className="w-4 h-4 animate-spin text-white" />
                                                                <span className="text-[10px] text-white/85">上传中</span>
                                                            </>
                                                        ) : (
                                                            <>
                                                                <X className="w-4 h-4 text-red-300" />
                                                                <span className="text-[10px] text-red-100">失败</span>
                                                            </>
                                                        )}
                                                    </div>
                                                )}
                                                {isReady && selectedRefs.includes(img.id) && (
                                                    <div className="absolute top-1 right-1 p-0.5 rounded-full bg-[var(--accent-primary)]">
                                                        <Check className="w-3 h-3 text-white" />
                                                    </div>
                                                )}
                                                {/* Mask edit button — only when mask is supported */}
                                                {maskSupported && isReady && (
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            if (maskData[img.id]) {
                                                                // Already has mask → remove it
                                                                removeMaskData(img.id);
                                                            } else {
                                                                // Open mask editor
                                                                setMaskEditingImageId(img.id);
                                                            }
                                                        }}
                                                        className={`absolute bottom-1 left-1 p-1 rounded-full transition-all ${
                                                            maskData[img.id]
                                                                ? 'bg-blue-500 text-white opacity-100 ring-1 ring-blue-400/50'
                                                                : 'bg-black/60 text-white/70 opacity-70 hover:opacity-100'
                                                        }`}
                                                        title={maskData[img.id] ? '已有遮罩 (点击移除)' : '编辑遮罩'}
                                                    >
                                                        <Paintbrush className="w-3 h-3" />
                                                    </button>
                                                )}
                                                {/* Delete button */}
                                                <button
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        void handleRemoveReferenceImage(img);
                                                    }}
                                                    className="absolute bottom-1 right-1 p-1 rounded-full bg-red-500/80 text-white opacity-0 hover:opacity-100 transition-opacity"
                                                    title={img.refId ? '从参考图库删除' : '移除参考图'}
                                                >
                                                    <X className="w-3 h-3" />
                                                </button>
                                            </div>
                                        );})}
                                    </div>
                                    {uploadedImages.filter((img) => img.refId).length < referenceTotal && (
                                        <button
                                            type="button"
                                            onClick={handleLoadMoreReferences}
                                            disabled={referenceLoadingMore}
                                            className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--accent-primary)] hover:text-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-60"
                                        >
                                            {referenceLoadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
                                            加载更多参考图（{uploadedImages.filter((img) => img.refId).length}/{referenceTotal}）
                                        </button>
                                    )}
                                </div>
                            ) : (
                                <p className="text-center text-[var(--text-muted)] text-sm py-4">
                                    还没有上传图片
                                </p>
                            )}

                            {/* Footer */}
                            <div className="flex items-center justify-between mt-4 pt-4 border-t border-[var(--border-subtle)]">
                                <span className="text-sm text-[var(--text-secondary)]">
                                    已选择 {selectedRefs.length} 张
                                </span>
                                <div className="flex gap-2">
                                    <button
                                        onClick={clearSelectedReferenceIds}
                                        className="px-3 py-1.5 rounded-lg text-sm bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)]"
                                    >
                                        清除选择
                                    </button>
                                    <button
                                        onClick={() => setShowImagePicker(false)}
                                        className="px-3 py-1.5 rounded-lg text-sm bg-[var(--accent-primary)] text-white hover:bg-[var(--accent-secondary)]"
                                    >
                                        确定
                                    </button>
                                </div>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Full-screen Mask Editor Overlay */}
            {maskEditingImageId && (() => {
                const editImg = uploadedImages.find(img => img.id === maskEditingImageId);
                if (!editImg) return null;
                return (
                    <MaskEditor
                        imageSrc={editImg.preview}
                        existingMask={maskData[maskEditingImageId]}
                        existingFeather={maskFeather[maskEditingImageId]}
                        onConfirm={(maskDataUrl, feather, inputMaxEdge) => {
                            setMaskData(maskEditingImageId, maskDataUrl, feather);
                            if (inputMaxEdge) {
                                setGenerateParams({ inputMaxEdge });
                            }
                            setMaskEditingImageId(null);
                        }}
                        onCancel={() => setMaskEditingImageId(null)}
                    />
                );
            })()}
        </>
    );
}
