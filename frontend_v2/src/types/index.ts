// Image generation result
export interface ImageItem {
    id: string;
    status: 'loading' | 'success' | 'error';  // Generation status
    error?: string;                            // Error message if failed
    localPath: string;           // Display URL (serve-image or data URI)
    savedFilePath?: string;      // Actual file system path for "Open Folder"
    relativePath?: string;       // Path relative to OPENAI_SAVE_DIR
    thumbnail?: string;
    width?: number;
    height?: number;
    prompt: string;
    apiType: 'apimart' | 'openai' | 'nanobanana2' | 'cliproxy' | 'sousaku' | 'other' | string;
    params: GenerateParams;
    createdAt: string;
    originalUrl?: string;
    isFavorite: boolean;
    tags: string[];
    jobId?: string;
    resultIndex?: number;
    providerIndex?: number;
    inputImages?: ReferenceImageInput[];
}

export interface ReferenceImageInput {
    url?: string;
    ref_id?: string;
    public_url?: string;
    name?: string;
}

// Generation parameters
export interface GenerateParams {
    ratio?: string;      // 1:1, 16:9, etc.
    quality?: string;    // 1K, 2K, 4K / standard, medium, hd
    size?: string;       // Pixel size for legacy providers, or aspect ratio for compatible proxies
    resolution?: string; // 1K, 2K, 4K for APIMart
    moderation?: string; // GPT-Image-2 Official: auto, low
    imageCount?: number; // 生成数量 1-10
    model?: string;       // Generic model id for configurable providers
    thinkingLevel?: string; // Nanobanana2: "High" or "Minimal"
    apimartModel?: string;  // APIMart: model name
    cliproxyModel?: string; // CLIProxyAPI: model name (default gpt-image-2)
    sousakuModel?: string;  // Sousaku: medium/high alias or raw model
    sousakuAutoOptimize?: boolean; // Sousaku: parameters.auto_optimize
    inputMaxEdge?: string;  // Explicit maximum pixel dimension for the input reference image (e.g. "2048")
}

// API request types
export interface GenerateRequest {
    prompt: string;
    size?: string;
    resolution?: string;
    quality?: string;
    moderation?: string;
    n?: number;  // 生成数量 1-10
    image_urls?: { url: string; ref_id?: string; public_url?: string }[];
    thinking_level?: string; // Nanobanana2: "High" or "Minimal"
    model?: string;          // APIMart: model name
    mask_data?: string;      // Mask PNG as base64 data URI (transparent = editable)
    feather?: number;        // Mask edge blur radius in px (0 = hard edge)
    input_max_edge?: number; // Override max edge dimension for preprocessing (CLIProxy only)
    auto_optimize?: boolean; // Sousaku prompt auto optimize
}

export interface GenerateResponse {
    success: boolean;
    // APIMart response structure
    code?: number;
    data?: {
        saved_path?: string;
        filename?: string;
        width?: number;
        height?: number;
        data_uri?: string;
        url?: string;
        b64_json?: string; // Add this for OpenAI/Sync response
        // APIMart task fields
        status?: string;
        task_id?: string;
    }[];
    // Nanobanana2 thought/draft images
    thought_images?: ThoughtImage[];
    // Legacy/OpenAI fields
    task_id?: string;
    error?: { message: string };
    save_dir?: string;
}

export interface BackendCapabilities {
    backendVersion: 'v1' | 'v2' | string;
    features: {
        galleryImport: boolean;
        localPickerImport?: boolean;
    };
}

// Thought/draft image from Nanobanana2 thinking stage
export interface ThoughtImage {
    id: string;
    data_uri: string;
    mime_type: string;
    timestamp: string;
}

export interface TaskStatusResponse {
    status: string;
    result?: {
        images?: { url: string }[];
    };
    error?: { message: string };
}

// Filter state
export interface FilterState {
    searchQuery: string;
    selectedDate: Date | null;
    selectedTags: string[];
    showFavoritesOnly: boolean;
}

// Upload state
export interface UploadedImage {
    id: string;
    file?: File;
    name?: string;
    preview: string;
    base64?: string;
    refId?: string;
    localUrl?: string;
    publicUrl?: string;
    contentType?: string;
    size?: number;
    status?: 'uploading' | 'ready' | 'failed';
    error?: string;
}

// ============ Mask / Inpainting Support ============

/** Declares which API + model combos support mask inpainting.
 *  If `model` is omitted, ALL models under that API are eligible. */
export interface MaskSupportEntry {
    api: string;
    model?: string;   // undefined = any model for that API
}

/** Central registry – add new entries here when a channel gains mask support. */
export const MASK_SUPPORTED_CONFIGS: MaskSupportEntry[] = [
    { api: 'cliproxy', model: 'gpt-image-2' },
    { api: 'apimart', model: 'gpt-image-2' },
    { api: 'apimart', model: 'gpt-image-2-official' },
    { api: 'nanobanana2' },  // all Gemini models support inpainting
];

/** Check whether the current api + model selection supports mask editing. */
export function isMaskSupported(api: string, model?: string): boolean {
    return MASK_SUPPORTED_CONFIGS.some(
        (c) => c.api === api && (c.model === undefined || c.model === model)
    );
}
