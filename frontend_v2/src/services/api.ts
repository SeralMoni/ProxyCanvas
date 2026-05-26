import axios from 'axios';
import type { BackendCapabilities, GenerateRequest, GenerateResponse, ImageItem, ReferenceImageInput, TaskStatusResponse } from '../types';

const api = axios.create({
    baseURL: '/api',
    timeout: 1200000, // 20 minutes for image generation
});

// APIMart API (async with polling)
export async function generateWithAPIMart(request: GenerateRequest): Promise<GenerateResponse> {
    const response = await api.post<GenerateResponse>('/generate', request);
    return response.data;
}

export async function getBackendCapabilities(): Promise<BackendCapabilities> {
    try {
        const response = await api.get<BackendCapabilities>('/capabilities', { timeout: 3000 });
        return response.data;
    } catch {
        return {
            backendVersion: 'v1',
            features: {
                galleryImport: false,
            },
        };
    }
}

// OpenAI API (sync)
export async function generateWithOpenAI(request: GenerateRequest): Promise<GenerateResponse> {
    const response = await api.post<GenerateResponse>('/generate-openai', request);
    return response.data;
}

export interface OpenAITaskItem {
    task_id: string;
    status: string;
    index?: number;
    data?: GenerateResponse['data'];
    error?: { message: string } | null;
}

export interface OpenAITaskCreateResponse {
    success: boolean;
    data: OpenAITaskItem[];
    error?: { message: string };
}

export interface OpenAITaskStatusResponse {
    success: boolean;
    data: OpenAITaskItem[];
    missing_ids?: string[];
    error?: { message: string };
}

// ChatGPT2API task mode (matches its own frontend more closely)
export async function generateOpenAITasks(request: GenerateRequest): Promise<OpenAITaskCreateResponse> {
    const response = await api.post<OpenAITaskCreateResponse>('/generate-openai-tasks', request);
    return response.data;
}

export async function checkOpenAITasks(taskIds: string[]): Promise<OpenAITaskStatusResponse> {
    const response = await api.get<OpenAITaskStatusResponse>('/openai-tasks', {
        params: { ids: taskIds.join(',') },
    });
    return response.data;
}

// Nanobanana2 API (Gemini 3.1 Flash Image - sync, concurrent)
export async function generateWithNanobanana2(request: GenerateRequest): Promise<GenerateResponse> {
    const response = await api.post<GenerateResponse>('/generate-nanobanana2', request);
    return response.data;
}

// CLIProxyAPI (Local Proxy for Codex/Claude/Gemini OAuth - OpenAI compatible, gpt-image-2)
export async function generateWithCliProxy(request: GenerateRequest): Promise<GenerateResponse> {
    const response = await api.post<GenerateResponse>('/generate-cliproxy', request);
    return response.data;
}

// Sousaku.ai API (async with partial-result polling)
export async function generateWithSousaku(request: GenerateRequest): Promise<GenerateResponse> {
    const response = await api.post<GenerateResponse>('/generate-sousaku', request);
    return response.data;
}

export async function checkSousakuTask(taskId: string): Promise<TaskStatusResponse> {
    const response = await api.get<TaskStatusResponse>(`/sousaku-task/${taskId}`);
    return response.data;
}

export interface GenerationJob {
    id: string;
    job_id: string;
    provider: string;
    status: 'queued' | 'submitting' | 'running' | 'saving' | 'succeeded' | 'failed' | 'cancelled' | 'timeout';
    prompt: string;
    params: Record<string, unknown>;
    input_images: ReferenceImageInput[];
    external_task_id?: string;
    progress: number;
    result: Array<Record<string, any>>;
    error?: string;
    attempts?: number;
    max_attempts?: number;
    created_at: string;
    updated_at: string;
    started_at?: string;
    finished_at?: string;
}

export interface GenerationJobResponse {
    success: boolean;
    job_id?: string;
    data?: GenerationJob;
    error?: { message: string };
}

export async function createGenerationJob(provider: string, request: Record<string, unknown>): Promise<GenerationJobResponse> {
    const response = await api.post<GenerationJobResponse>('/jobs', {
        provider,
        ...request,
    }, {
        timeout: 30000,
    });
    return response.data;
}

export async function checkGenerationJob(jobId: string): Promise<GenerationJobResponse> {
    const response = await api.get<GenerationJobResponse>(`/jobs/${jobId}`, {
        timeout: 30000,
    });
    return response.data;
}

export async function listGenerationJobs(params?: {
    status?: string;
    active?: boolean;
    limit?: number;
}): Promise<{ success: boolean; data: GenerationJob[]; error?: { message: string } }> {
    const response = await api.get('/jobs', {
        params: {
            status: params?.status || undefined,
            active: params?.active ? '1' : undefined,
            limit: params?.limit || 100,
        },
        timeout: 30000,
    });
    return response.data;
}

export async function deleteGenerationJob(jobId: string): Promise<GenerationJobResponse> {
    const response = await api.delete<GenerationJobResponse>(`/jobs/${jobId}`, {
        timeout: 30000,
    });
    return response.data;
}

export async function deleteGenerationJobs(options?: { includeActive?: boolean }): Promise<{
    success: boolean;
    data?: { deleted: number };
    error?: { message: string };
}> {
    const response = await api.delete('/jobs', {
        data: { include_active: options?.includeActive ?? true },
        timeout: 30000,
    });
    return response.data;
}

export interface ProviderAccount {
    id: string;
    provider: string;
    label: string;
    status: 'available' | 'busy' | 'low_quota' | 'invalid' | 'disabled';
    quota?: {
        total?: number;
        remaining?: number;
        unit?: string;
    };
    running_jobs?: number;
    last_used_at?: string;
    tags?: string[];
    metadata?: Record<string, any>;
}

export async function listProviderAccounts(provider = 'sousaku', options?: { refresh?: boolean }): Promise<{
    success: boolean;
    provider: string;
    count?: number;
    updated_at?: string;
    low_credit_threshold?: number;
    data: ProviderAccount[];
    error?: { message: string };
}> {
    const response = await api.get('/provider-accounts', {
        params: { provider, refresh: options?.refresh ? '1' : undefined },
        timeout: options?.refresh ? 120000 : 30000,
    });
    return response.data;
}

export async function addSousakuTokens(tokens: string): Promise<{
    success: boolean;
    added?: number;
    skipped?: number;
    total?: number;
    refreshed?: number;
    error?: { message: string };
}> {
    const response = await api.post('/provider-accounts/sousaku/tokens', { tokens }, {
        timeout: 120000,
    });
    return response.data;
}

export async function refreshSousakuAccount(accountId: string): Promise<{
    success: boolean;
    provider?: string;
    account_id?: string;
    error?: { message: string };
}> {
    const response = await api.post(`/provider-accounts/sousaku/${encodeURIComponent(accountId)}/refresh`, {}, {
        timeout: 120000,
    });
    return response.data;
}

export async function updateSousakuAccount(accountId: string, updates: { disabled: boolean }): Promise<{
    success: boolean;
    provider?: string;
    account_id?: string;
    disabled?: boolean;
    error?: { message: string };
}> {
    const response = await api.patch(`/provider-accounts/sousaku/${encodeURIComponent(accountId)}`, updates, {
        timeout: 30000,
    });
    return response.data;
}

export async function deleteSousakuAccount(accountId: string): Promise<{
    success: boolean;
    provider?: string;
    account_id?: string;
    error?: { message: string };
}> {
    const response = await api.delete(`/provider-accounts/sousaku/${encodeURIComponent(accountId)}`, {
        timeout: 30000,
    });
    return response.data;
}

export interface SettingValue<T> {
    value: T;
    source: string;
    resolved?: string;
}

export interface BackendSettings {
    ui: {
        prompt: {
            autoClear: SettingValue<boolean>;
        };
        gallery: {
            columns: SettingValue<number>;
            displayMode: SettingValue<'waterfall' | 'pagination'>;
            pageSize: SettingValue<number>;
            deleteLocalFile: SettingValue<boolean>;
            deleteImportedOriginal: SettingValue<boolean>;
            selectionColor: SettingValue<string>;
            selectionBoxColor: SettingValue<string>;
            tagColor: SettingValue<string>;
        };
    };
    paths: {
        projectRoot: SettingValue<string>;
        saveDir: SettingValue<string>;
        jobsDb: SettingValue<string>;
        galleryDb: SettingValue<string>;
    };
    server: {
        backendPort: SettingValue<number>;
        frontendPort: SettingValue<number>;
        useReloader: SettingValue<boolean>;
    };
    gallery: {
        thumbnailWidth: SettingValue<number>;
        thumbnailQuality: SettingValue<number>;
        thumbnailCacheMaxGb: SettingValue<number>;
    };
    jobs: {
        workerEnabled: SettingValue<boolean>;
        maxWorkers: SettingValue<number>;
        pollIntervalSeconds: SettingValue<number>;
        defaultTimeoutSeconds: SettingValue<number>;
        sousakuStaleTaskSeconds: SettingValue<number>;
        providerLimits: SettingValue<Record<string, number>>;
    };
    network: {
        httpProxies: SettingValue<Record<string, string> | null>;
        publicUrlTtlSeconds: SettingValue<number>;
    };
    logging: {
        level: SettingValue<'DEBUG' | 'INFO' | 'OK' | 'WARN' | 'ERROR'>;
        color: SettingValue<boolean>;
        sousakuProgressPanel: SettingValue<boolean>;
    };
    configFiles: Record<string, { path: string; exists: boolean }>;
}

export interface StorageUsage {
    saveDir: string;
    gallery: {
        records: number;
        files: number;
        bytes: number;
        missing: number;
        imports: {
            path: string;
            files: number;
            bytes: number;
        };
    };
    thumbnailCache: {
        path: string;
        files: number;
        bytes: number;
        maxBytes: number;
    };
    referenceLibrary?: {
        path: string;
        files: number;
        bytes: number;
        assets: {
            path: string;
            files: number;
            bytes: number;
        };
        thumbnails: {
            path: string;
            files: number;
            bytes: number;
        };
    };
}

export interface RuntimeProvider {
    id: string;
    label: string;
    type: string;
    protocol?: string;
    enabled: boolean;
    source: string;
    baseUrl: string;
    apiKey: string;
    defaultModel: string;
    models: Array<{
        value: string;
        label?: string;
        defaults?: Record<string, unknown>;
        controls?: Array<{
            key: string;
            label?: string;
            type?: 'select' | 'boolean' | 'number';
            options?: Array<string | number | boolean | { value: string | number | boolean; label?: string }>;
            min?: number;
            max?: number;
            step?: number;
        }>;
        constraints?: Record<string, unknown>;
        features?: Record<string, unknown>;
        payload?: Record<string, unknown>;
    }>;
    capabilities: string[];
    notes?: string;
    configPath?: string;
    stream?: boolean;
    timeoutSeconds?: number;
    badgeColor?: string;
    builtin?: boolean;
}

export async function loadBackendSettings(): Promise<BackendSettings> {
    const response = await api.get<{ success: boolean; data: BackendSettings }>('/settings', {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error('Failed to load settings');
    }
    return response.data.data;
}

export interface AppSettingsPatch {
    server?: {
        backendPort?: number;
        frontendPort?: number;
        useReloader?: boolean;
    };
    storage?: {
        saveDir?: string;
    };
    ui?: {
        prompt?: {
            autoClear?: boolean;
        };
        gallery?: {
            columns?: number;
            displayMode?: 'waterfall' | 'pagination';
            pageSize?: number;
            deleteLocalFile?: boolean;
            deleteImportedOriginal?: boolean;
            selectionColor?: string;
            selectionBoxColor?: string;
            tagColor?: string;
        };
    };
    gallery?: {
        thumbnailWidth?: number;
        thumbnailQuality?: number;
        thumbnailCacheMaxGb?: number;
    };
    jobs?: {
        workerEnabled?: boolean;
        maxWorkers?: number;
        pollIntervalSeconds?: number;
        defaultTimeoutSeconds?: number;
        sousakuStaleTaskSeconds?: number;
        providerLimits?: Record<string, number>;
    };
    network?: {
        httpProxies?: Record<string, string> | null;
        publicUrlTtlSeconds?: number;
    };
    logging?: {
        level?: 'DEBUG' | 'INFO' | 'OK' | 'WARN' | 'ERROR';
        color?: boolean;
        sousakuProgressPanel?: boolean;
    };
}

export async function saveBackendSettings(patch: AppSettingsPatch): Promise<AppSettingsPatch> {
    const response = await api.patch<{ success: boolean; data: AppSettingsPatch }>('/settings', patch, {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error('Failed to save settings');
    }
    return response.data.data;
}

export async function resetBackendSettings(): Promise<AppSettingsPatch> {
    const response = await api.post<{ success: boolean; data: AppSettingsPatch }>('/settings/reset', {}, {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error('Failed to reset settings');
    }
    return response.data.data;
}

export async function loadStorageUsage(): Promise<StorageUsage> {
    const response = await api.get<{ success: boolean; data: StorageUsage; error?: { message?: string } }>('/storage/usage', {
        timeout: 120000,
    });
    if (!response.data.success) {
        throw new Error(response.data.error?.message || 'Failed to load storage usage');
    }
    return response.data.data;
}

export async function clearStorageCache(cacheName: 'thumbnails'): Promise<void> {
    const response = await api.post<{ success: boolean; error?: { message?: string } }>(`/storage/cache/${cacheName}/clear`, {}, {
        timeout: 120000,
    });
    if (!response.data.success) {
        throw new Error(response.data.error?.message || 'Failed to clear cache');
    }
}

export async function loadRuntimeProviders(): Promise<RuntimeProvider[]> {
    const response = await api.get<{ success: boolean; data: RuntimeProvider[] }>('/providers', {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error('Failed to load providers');
    }
    return response.data.data;
}

export async function createRuntimeProvider(payload: {
    id?: string;
    label: string;
    type?: string;
    protocol?: string;
    baseUrl: string;
    apiKey?: string;
    defaultModel?: string;
    stream?: boolean;
    badgeColor?: string;
    models?: RuntimeProvider['models'];
}): Promise<RuntimeProvider> {
    const response = await api.post<{ success: boolean; data: RuntimeProvider; error?: { message?: string } }>('/providers', payload, {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error(response.data.error?.message || 'Failed to create provider');
    }
    return response.data.data;
}

export async function saveRuntimeProvider(providerId: string, patch: {
    label?: string;
    type?: string;
    protocol?: string;
    enabled?: boolean;
    baseUrl?: string;
    apiKey?: string;
    defaultModel?: string;
    models?: RuntimeProvider['models'];
    capabilities?: string[];
    configPath?: string;
    notes?: string;
    stream?: boolean;
    timeoutSeconds?: number;
    badgeColor?: string;
}): Promise<RuntimeProvider> {
    const response = await api.patch<{ success: boolean; data: RuntimeProvider }>(`/providers/${providerId}`, patch, {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error('Failed to save provider');
    }
    return response.data.data;
}

export async function deleteRuntimeProvider(providerId: string): Promise<{ success: boolean }> {
    const response = await api.delete<{ success: boolean }>(`/providers/${providerId}`, {
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error('Failed to delete provider');
    }
    return response.data;
}

// Save a thought/draft image to local storage
export async function saveThoughtImage(dataUri: string): Promise<{ saved_path: string; filename: string }> {
    const response = await api.post<{ success: boolean; saved_path: string; filename: string }>('/save-thought-image', { data_uri: dataUri });
    return response.data;
}

// Check task status (for APIMart polling)
export async function checkTaskStatus(taskId: string): Promise<TaskStatusResponse> {
    const response = await api.get<TaskStatusResponse>(`/task/${taskId}`);
    return response.data;
}

// Poll task until complete
export async function pollTaskUntilComplete(
    taskId: string,
    onProgress?: (status: string) => void,
    maxAttempts = 200,  // 200 attempts × 3s = 10 minutes max
    interval = 3000     // 3 seconds between polls
): Promise<TaskStatusResponse> {
    for (let i = 0; i < maxAttempts; i++) {
        const response = await checkTaskStatus(taskId);

        // APIMart task query: data is OBJECT {status}, not array [{status}]
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const respAny = response as any;
        // Handle both: data as object (task query) or data as array (generation)
        const dataObj = respAny.data;
        const statusFromData = Array.isArray(dataObj)
            ? dataObj[0]?.status
            : dataObj?.status;
        const status = response.status || statusFromData || '';

        if (onProgress) {
            onProgress(status);
        }

        // Case-insensitive status check for APIMart compatibility
        const statusLower = status?.toLowerCase();

        if (statusLower === 'completed' || statusLower === 'success' || statusLower === 'succeeded') {
            return response;
        }

        if (statusLower === 'failed' || response.error) {
            throw new Error(response.error?.message || 'Task failed');
        }

        await new Promise((resolve) => setTimeout(resolve, interval));
    }

    throw new Error('Task polling timeout');
}

// Get balance
export async function getBalance(): Promise<{ balance: number }> {
    const response = await api.get('/balance');
    return response.data;
}

// Upload image to image hosting (auto-compresses if >10MB)
export async function uploadImage(file: File): Promise<{
    success: boolean;
    url?: string;
    message?: string;
    compressed?: boolean;
    original_size?: number;
    final_size?: number;
}> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await api.post('/upload-image', formData, {
        // Don't set Content-Type manually - axios will set it with correct boundary
        timeout: 60000,
    });

    return response.data;
}

export interface ReferenceImageAsset {
    ref_id: string;
    name: string;
    local_url: string;
    preview_url: string;
    content_type: string;
    suffix: string;
    size: number;
    width?: number;
    height?: number;
    public_urls?: Record<string, { url?: string }>;
    created_at: number;
    last_used_at: number;
}

export async function uploadReferenceImage(file: File): Promise<ReferenceImageAsset> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post<{ success: boolean; data: ReferenceImageAsset; error?: { message?: string } }>('/reference-images', formData, {
        timeout: 120000,
    });
    if (!response.data.success) {
        throw new Error(response.data.error?.message || '参考图上传失败');
    }
    return response.data.data;
}

export async function importReferenceImageUrl(url: string): Promise<ReferenceImageAsset> {
    const response = await api.post<{ success: boolean; data: ReferenceImageAsset; error?: { message?: string } }>('/reference-images', { url }, {
        timeout: 120000,
    });
    if (!response.data.success) {
        throw new Error(response.data.error?.message || '参考图导入失败');
    }
    return response.data.data;
}

export async function loadReferenceImages(limit = 120, offset = 0): Promise<{ items: ReferenceImageAsset[]; total: number }> {
    const response = await api.get<{ success: boolean; data: ReferenceImageAsset[]; total?: number; error?: { message?: string } }>('/reference-images', {
        params: { limit, offset },
        timeout: 30000,
    });
    if (!response.data.success) {
        throw new Error(response.data.error?.message || '参考图库加载失败');
    }
    return {
        items: response.data.data,
        total: response.data.total ?? response.data.data.length,
    };
}

export async function deleteReferenceImage(refId: string): Promise<void> {
    try {
        const response = await api.delete<{ success: boolean; error?: { message?: string } }>(`/reference-images/${encodeURIComponent(refId)}`, {
            timeout: 30000,
        });
        if (!response.data.success) {
            throw new Error(response.data.error?.message || '参考图删除失败');
        }
    } catch (error) {
        if (axios.isAxiosError(error)) {
            if (error.response?.status === 404) return;
            const data = error.response?.data as { error?: { message?: string }; message?: string } | undefined;
            throw new Error(data?.error?.message || data?.message || '参考图删除失败');
        }
        throw error;
    }
}

// Convert file to base64 data URI
export function fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

// Unified response handler
export async function handleGenerationResponse(response: GenerateResponse): Promise<{ url: string; localPath?: string }> {
    // Returns first image only - for backward compatibility
    const results = await handleMultipleImagesResponse(response);
    if (results.length > 0) {
        return results[0];
    }
    throw new Error('生成结果为空');
}

// Handle multiple images response
export async function handleMultipleImagesResponse(response: GenerateResponse): Promise<Array<{ url: string; localPath?: string }>> {
    const results: Array<{ url: string; localPath?: string }> = [];

    // 1. Check for Sync Response (Data available immediately)
    if (response.data && response.data.length > 0) {
        // Process ALL images in data array
        for (const result of response.data) {
            const directUrl = result.url || result.b64_json || result.data_uri;
            const localPath = result.saved_path;

            if (directUrl || localPath) {
                results.push({ url: directUrl || '', localPath });
            }
        }

        if (results.length > 0) {
            return results;
        }
    }

    // 2. Check for Async Task (Needs polling)
    // APIMart nests task_id in data[0], legacy/OpenAI might use root task_id
    const taskId = response.task_id || (response.data && response.data[0]?.task_id);

    if (taskId) {
        console.log('Async task submitted, ID:', taskId);
        const taskResult = await pollTaskUntilComplete(taskId);
        console.log('Task completed, result:', JSON.stringify(taskResult, null, 2));

        // Backend returns: {code, data: {status, result: {images: [{url, saved_path}]}}}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const respAny = taskResult as any;
        const data = respAny.data || respAny;  // Handle both wrapped and unwrapped
        const images = data?.result?.images || taskResult.result?.images;

        let imageUrl: string | undefined;
        let localPath: string | undefined;

        if (images && images.length > 0) {
            const imgData = images[0];
            // Backend normalizes url to string, but handle array just in case
            const urlField = imgData.url;
            imageUrl = Array.isArray(urlField) ? urlField[0] : urlField;
            localPath = imgData.saved_path;
            console.log('📦 Found image:', { imageUrl: imageUrl?.substring(0, 50), localPath });
        }

        if (imageUrl || localPath) {
            return [{ url: imageUrl || '', localPath }];
        } else {
            console.error('Task result invalid - no image URL found:', taskResult);
            throw new Error(respAny.error?.message || taskResult.error?.message || '生成结果为空');
        }
    }

    // 3. Error Case
    console.error('Invalid response structure:', response);
    throw new Error(response.error?.message || '生成失败: 未收到有效数据或任务ID');
}

export interface GalleryData {
    images: ImageItem[];
    tags: string[];
}

export interface ImportGalleryImagesOptions {
    files: File[];
    prompt?: string;
    apiType?: ImageItem['apiType'];
    ratio?: string;
    quality?: string;
    tags?: string[];
}

export async function importGalleryImages(options: ImportGalleryImagesOptions): Promise<ImageItem[]> {
    const formData = new FormData();
    options.files.forEach((file) => formData.append('files', file));
    formData.append('prompt', options.prompt || '外部导入图片');
    formData.append('apiType', options.apiType || 'other');
    formData.append('ratio', options.ratio || 'auto');
    formData.append('quality', options.quality || 'imported');
    if (options.tags && options.tags.length > 0) {
        formData.append('tags', options.tags.join(','));
    }

    const response = await api.post<{ success: boolean; data?: ImageItem[]; message?: string }>('/gallery/import', formData, {
        timeout: 120000,
    });

    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '导入失败');
    }

    return response.data.data;
}

export interface ImportLocalPickerResult {
    images: ImageItem[];
    deletedOriginalCount: number;
    deleteOriginalSkippedCount: number;
}

export interface PickedLocalFile {
    token: string;
    name: string;
    previewUrl: string;
}

export async function pickLocalGalleryFiles(): Promise<PickedLocalFile[]> {
    const response = await api.post<{ success: boolean; data?: PickedLocalFile[]; message?: string }>('/gallery/pick-local-files', {}, {
        timeout: 0,
    });

    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '选择文件失败');
    }

    return response.data.data;
}

export async function importPickedLocalGalleryFiles(options: Omit<ImportGalleryImagesOptions, 'files'> & { tokens: string[]; deleteOriginal?: boolean }): Promise<ImportLocalPickerResult> {
    let response;
    try {
        response = await api.post<{
            success: boolean;
            data?: ImageItem[];
            deletedOriginalCount?: number;
            deleteOriginalSkippedCount?: number;
            message?: string;
        }>('/gallery/import-picked-local-files', {
            tokens: options.tokens,
            prompt: options.prompt || '外部导入图片',
            apiType: options.apiType || 'other',
            ratio: options.ratio || 'auto',
            quality: options.quality || 'imported',
            tags: options.tags || [],
            deleteOriginal: options.deleteOriginal || false,
        }, {
            timeout: 0,
        });
    } catch (error) {
        if (axios.isAxiosError(error) && error.response?.data?.message) {
            throw new Error(error.response.data.message);
        }
        throw error;
    }

    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '导入失败');
    }

    return {
        images: response.data.data,
        deletedOriginalCount: response.data.deletedOriginalCount || 0,
        deleteOriginalSkippedCount: response.data.deleteOriginalSkippedCount || 0,
    };
}

export async function loadGallery(options?: { limit?: number; offset?: number }): Promise<GalleryData> {
    const response = await api.get<{ success: boolean; data: GalleryData }>('/gallery', {
        params: {
            limit: options?.limit,
            offset: options?.offset,
        },
    });
    if (response.data.success) {
        return response.data.data;
    }
    throw new Error('Failed to load gallery');
}

// Keep gallery writes ordered so a late save cannot recreate a just-deleted image.
let _galleryMutationQueue: Promise<unknown> = Promise.resolve();
const _deletedGalleryImageIds = new Set<string>();

function enqueueGalleryMutation<T>(task: () => Promise<T>): Promise<T> {
    const run = _galleryMutationQueue.then(task, task);
    _galleryMutationQueue = run.catch(() => {});
    return run;
}

export async function saveToGallery(image: ImageItem, options?: { force?: boolean }): Promise<void> {
    if (options?.force) {
        _deletedGalleryImageIds.delete(image.id);
    }
    if (_deletedGalleryImageIds.has(image.id)) return;
    await enqueueGalleryMutation(async () => {
        if (options?.force) {
            _deletedGalleryImageIds.delete(image.id);
        }
        if (_deletedGalleryImageIds.has(image.id)) return;
        await api.post('/gallery', image);
    });
}

export async function deleteFromGallery(imageId: string, deleteLocal?: boolean): Promise<void> {
    _deletedGalleryImageIds.add(imageId);
    const params = deleteLocal ? '?delete_local=true' : '';
    await enqueueGalleryMutation(async () => {
        await api.delete(`/gallery/${imageId}${params}`);
    });
}

export async function updateGalleryTags(tags: string[]): Promise<void> {
    await api.post('/gallery/tags', { tags });
}

export async function batchDeleteGalleryImages(ids: string[], deleteLocal: boolean): Promise<{
    deleted: number;
    localDeleted: number;
    localSkipped: number;
}> {
    ids.forEach((id) => _deletedGalleryImageIds.add(id));
    const response = await enqueueGalleryMutation(() => api.post<{ success: boolean; data?: { deleted: number; localDeleted: number; localSkipped: number }; message?: string }>(
        '/gallery/batch/delete',
        { ids, deleteLocal },
        { timeout: 120000 },
    ));
    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '批量删除失败');
    }
    return response.data.data;
}

export async function batchUpdateGalleryTags(ids: string[], options: { add?: string[]; remove?: string[] }): Promise<{ updated: number }> {
    const response = await api.post<{ success: boolean; data?: { updated: number }; message?: string }>(
        '/gallery/batch/tags',
        { ids, add: options.add || [], remove: options.remove || [] },
        { timeout: 120000 },
    );
    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '批量标签更新失败');
    }
    return response.data.data;
}

export async function batchFavoriteGalleryImages(ids: string[], favorite: boolean): Promise<{ updated: number; favorite: boolean }> {
    const response = await api.post<{ success: boolean; data?: { updated: number; favorite: boolean }; message?: string }>(
        '/gallery/batch/favorite',
        { ids, favorite },
        { timeout: 120000 },
    );
    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '批量收藏失败');
    }
    return response.data.data;
}

export async function batchExportGalleryImages(ids: string[]): Promise<{ exported: number; skipped: number; directory: string; cancelled?: boolean }> {
    const response = await api.post<{ success: boolean; data?: { exported: number; skipped: number; directory: string; cancelled?: boolean }; message?: string }>(
        '/gallery/batch/export',
        { ids },
        { timeout: 0 },
    );
    if (!response.data.success || !response.data.data) {
        throw new Error(response.data.message || '导出失败');
    }
    return response.data.data;
}
