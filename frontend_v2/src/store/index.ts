import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';
import type { BackendCapabilities, ImageItem, FilterState, UploadedImage, GenerateParams, ThoughtImage } from '../types';
import { getBackendCapabilities, importReferenceImageUrl, loadBackendSettings, loadGallery, loadReferenceImages, resetBackendSettings, saveBackendSettings, saveToGallery, deleteFromGallery, deleteReferenceImage, updateGalleryTags, uploadReferenceImage } from '../services/api';

export type GalleryColumnSize = 5 | 6 | 7;
export type ApiType = string;
export type GalleryDisplayMode = 'waterfall' | 'pagination';

export const DEFAULT_GALLERY_PAGE_SIZE = 60;
export const MIN_GALLERY_PAGE_SIZE = 20;
export const MAX_GALLERY_PAGE_SIZE = 240;
export const DEFAULT_GALLERY_SELECTION_COLOR = '#fdba74';
export const DEFAULT_GALLERY_SELECTION_BOX_COLOR = '#fef08a';
export const DEFAULT_GALLERY_TAG_COLOR = '#f43f5e';

function clampGalleryPageSize(value: number) {
    const parsed = Number.isFinite(value) ? Math.round(value) : DEFAULT_GALLERY_PAGE_SIZE;
    return Math.min(MAX_GALLERY_PAGE_SIZE, Math.max(MIN_GALLERY_PAGE_SIZE, parsed));
}

function applyUiSettings(set: (state: Partial<AppState>) => void, settings: {
    ui?: {
        prompt?: { autoClear?: boolean };
        gallery?: {
            columns?: number;
            displayMode?: GalleryDisplayMode;
            pageSize?: number;
            deleteLocalFile?: boolean;
            deleteImportedOriginal?: boolean;
            selectionColor?: string;
            selectionBoxColor?: string;
            tagColor?: string;
        };
    };
}) {
    const prompt = settings.ui?.prompt || {};
    const gallery = settings.ui?.gallery || {};
    set({
        autoClearPrompt: prompt.autoClear ?? false,
        galleryColumns: (gallery.columns || 5) as GalleryColumnSize,
        galleryDisplayMode: gallery.displayMode || 'waterfall',
        galleryPageSize: clampGalleryPageSize(gallery.pageSize ?? DEFAULT_GALLERY_PAGE_SIZE),
        deleteLocalFile: gallery.deleteLocalFile ?? false,
        deleteImportedOriginal: gallery.deleteImportedOriginal ?? false,
        gallerySelectionColor: gallery.selectionColor || DEFAULT_GALLERY_SELECTION_COLOR,
        gallerySelectionBoxColor: gallery.selectionBoxColor || DEFAULT_GALLERY_SELECTION_BOX_COLOR,
        galleryTagColor: gallery.tagColor || DEFAULT_GALLERY_TAG_COLOR,
    });
}

function deriveTags(images: ImageItem[]): string[] {
    const tags = new Set<string>();
    images.forEach((image) => {
        image.tags.forEach((tag) => {
            const cleaned = tag.trim();
            if (cleaned) tags.add(cleaned);
        });
    });
    return Array.from(tags).sort();
}

function deriveTagsAfterDelete(previousTags: string[], remainingImages: ImageItem[], deletedImages: ImageItem[]) {
    const touchedTags = new Set<string>();
    deletedImages.forEach((image) => {
        image.tags.forEach((tag) => {
            const cleaned = tag.trim();
            if (cleaned) touchedTags.add(cleaned);
        });
    });
    if (touchedTags.size === 0) {
        return previousTags;
    }

    const stillUsedTags = new Set<string>();
    remainingImages.forEach((image) => {
        image.tags.forEach((tag) => {
            const cleaned = tag.trim();
            if (touchedTags.has(cleaned)) {
                stillUsedTags.add(cleaned);
            }
        });
    });

    return previousTags.filter((tag) => !touchedTags.has(tag) || stillUsedTags.has(tag));
}

function mergeTags(existing: string[], images: ImageItem[]): string[] {
    const tags = new Set(existing);
    let changed = false;
    images.forEach((image) => {
        image.tags.forEach((tag) => {
            const cleaned = tag.trim();
            if (cleaned && !tags.has(cleaned)) {
                tags.add(cleaned);
                changed = true;
            }
        });
    });
    return changed ? Array.from(tags).sort() : existing;
}

function gallerySortValue(image: ImageItem) {
    const time = new Date(image.createdAt).getTime();
    return Number.isFinite(time) ? time : 0;
}

function galleryResultIndex(image: ImageItem) {
    const raw = (image as ImageItem & { resultIndex?: unknown }).resultIndex;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : Number.MAX_SAFE_INTEGER;
}

function sortGalleryImages(images: ImageItem[]) {
    return [...images].sort((a, b) => {
        const byTime = gallerySortValue(b) - gallerySortValue(a);
        if (byTime !== 0) return byTime;
        const byResultIndex = galleryResultIndex(a) - galleryResultIndex(b);
        if (byResultIndex !== 0) return byResultIndex;
        return a.id.localeCompare(b.id);
    });
}

const DEFAULT_PER_API_PARAMS: Record<string, GenerateParams> = {
    openai: {
        ratio: '16:9',
        imageCount: 1,
    },
    cliproxy: {
        cliproxyModel: 'gpt-image-2',
        ratio: '16:9',
        resolution: '2K',
        quality: 'high',
        imageCount: 1,
    },
    nanobanana2: {
        ratio: '16:9',
        quality: 'hd',
        imageCount: 1,
        thinkingLevel: 'High',
    },
    apimart: {
        apimartModel: 'gemini-3-pro-image-preview',
        ratio: '16:9',
        resolution: '4K',
        quality: 'high',
        moderation: 'low',
    },
    sousaku: {
        sousakuModel: 'gpt-image-2',
        ratio: '1:1',
        resolution: '4K',
        sousakuAutoOptimize: true,
        imageCount: 1,
    },
};

const DEFAULT_SELECTED_MODELS: Record<string, string> = {
    openai: 'gpt-image-2',
    cliproxy: 'gpt-image-2',
    nanobanana2: 'gemini-3.1-flash-image',
    apimart: 'gemini-3-pro-image-preview',
    sousaku: 'gpt-image-2',
};

function pickSharedGenerateParams(params: Partial<GenerateParams>): Partial<GenerateParams> {
    const shared: Partial<GenerateParams> = {};
    if (params.ratio !== undefined) shared.ratio = params.ratio;
    if (params.quality !== undefined) shared.quality = params.quality;
    if (params.size !== undefined) shared.size = params.size;
    if (params.resolution !== undefined) shared.resolution = params.resolution;
    if (params.moderation !== undefined) shared.moderation = params.moderation;
    if (params.imageCount !== undefined) shared.imageCount = params.imageCount;
    if (params.inputMaxEdge !== undefined) shared.inputMaxEdge = params.inputMaxEdge;
    return shared;
}

function canonicalResolution(value: unknown): string | undefined {
    if (typeof value !== 'string') return undefined;
    const normalized = value.trim().toUpperCase();
    return ['1K', '2K', '4K'].includes(normalized) ? normalized : value;
}

function normalizeGenerateParams<T extends Partial<GenerateParams>>(params: T): T {
    if (params.resolution === undefined) return params;
    const resolution = canonicalResolution(params.resolution);
    return resolution === params.resolution ? params : { ...params, resolution } as T;
}

function mergeGenerateParams(
    api: ApiType,
    model: string,
    state: Pick<AppState, 'sharedGenerateParams' | 'perApiParams' | 'perApiModelParams'>,
): GenerateParams {
    return normalizeGenerateParams({
        ...(DEFAULT_PER_API_PARAMS[api] || { ratio: '16:9', quality: 'high', imageCount: 1 }),
        ...(state.perApiParams?.[api] || {}),
        ...(state.perApiModelParams?.[api]?.[model] || {}),
        ...(state.sharedGenerateParams || {}),
    } as GenerateParams);
}

function modelParamKey(api: ApiType): 'apimartModel' | 'cliproxyModel' | 'sousakuModel' | 'model' {
    if (api === 'apimart') return 'apimartModel';
    if (api === 'cliproxy') return 'cliproxyModel';
    if (api === 'sousaku') return 'sousakuModel';
    return 'model';
}

function buildModelSnapshot(api: ApiType, model: string, base?: Partial<GenerateParams>): GenerateParams {
    const snapshot = normalizeGenerateParams({
        ...(DEFAULT_PER_API_PARAMS[api] || { ratio: '16:9', quality: 'high', imageCount: 1 }),
        ...(base || {}),
    } as GenerateParams);
    if (api === 'apimart') snapshot.apimartModel = model;
    if (api === 'cliproxy') snapshot.cliproxyModel = model;
    if (api === 'sousaku') snapshot.sousakuModel = model;
    return snapshot;
}

function resolveSelectedModel(api: ApiType, state: Pick<AppState, 'selectedModelByApi' | 'perApiModelParams' | 'perApiParams'>): string {
    const modelKey = modelParamKey(api);
    const currentParams = state.perApiParams?.[api] as Record<string, unknown> | undefined;
    const currentModel = currentParams?.[modelKey];
    return (
        state.selectedModelByApi?.[api] ||
        (typeof currentModel === 'string' ? currentModel : '') ||
        DEFAULT_SELECTED_MODELS[api] || 'gpt-image-2'
    );
}

interface AppState {
    // Images (loaded from backend JSON)
    images: ImageItem[];
    galleryLoaded: boolean;
    backendCapabilities: BackendCapabilities;
    loadBackendCapabilities: () => Promise<void>;
    loadUiSettingsFromServer: () => Promise<void>;
    resetUiSettingsToDefaults: () => Promise<void>;
    loadGalleryFromServer: () => Promise<void>;
    reloadGalleryFromServer: () => Promise<void>;
    addImage: (image: ImageItem) => void;
    addImagesLocal: (images: ImageItem[]) => void;
    addImportedImages: (images: ImageItem[]) => void;
    removeImagesLocal: (ids: string[]) => void;
    addTagsToImagesLocal: (ids: string[], tags: string[]) => void;
    removeTagsFromImagesLocal: (ids: string[], tags: string[]) => void;
    setImagesFavoriteLocal: (ids: string[], favorite: boolean) => void;
    updateImage: (id: string, updates: Partial<ImageItem>) => boolean;
    removeImage: (id: string) => void;
    removeImageLocalOnly: (id: string) => void;
    toggleFavorite: (id: string) => void;
    addTagToImage: (id: string, tag: string) => void;
    removeTagFromImage: (id: string, tag: string) => void;

    // All available tags
    allTags: string[];
    addTag: (tag: string) => void;
    removeTag: (tag: string) => void;

    // Filters
    filters: FilterState;
    setSearchQuery: (query: string) => void;
    setSelectedDate: (date: Date | null) => void;
    setSelectedTags: (tags: string[]) => void;
    toggleFavoritesOnly: () => void;

    // Generation state
    selectedApi: ApiType;
    setSelectedApi: (api: ApiType) => void;
    sharedGenerateParams: Partial<GenerateParams>;
    perApiParams: Record<string, GenerateParams>;
    selectedModelByApi: Record<string, string>;
    perApiModelParams: Record<string, Record<string, GenerateParams>>;
    setSelectedModel: (model: string) => void;
    setGenerateParams: (params: Partial<GenerateParams>) => void;

    // Upload state
    uploadedImages: UploadedImage[];
    selectedReferenceIds: string[];
    addUploadedImage: (image: UploadedImage) => void;
    updateUploadedImage: (id: string, updates: Partial<UploadedImage>) => void;
    removeUploadedImage: (id: string) => Promise<void>;
    clearUploadedImages: () => void;
    loadReferenceImagesFromServer: (options?: { reset?: boolean; limit?: number }) => Promise<{ loaded: number; total: number }>;
    uploadReferenceImages: (files: File[]) => Promise<void>;
    addReferenceUrl: (url: string) => void;
    setSelectedReferenceIds: (ids: string[]) => void;
    clearSelectedReferenceIds: () => void;
    toggleSelectedReferenceId: (id: string) => void;

    // Mask state (in-memory only, NOT persisted)
    maskData: Record<string, string>;      // imageId -> mask PNG dataURL
    maskFeather: Record<string, number>;   // imageId -> feather radius
    setMaskData: (imageId: string, dataUrl: string, feather?: number) => void;
    removeMaskData: (imageId: string) => void;
    clearMaskData: () => void;

    // UI state
    isGenerating: boolean;
    setIsGenerating: (value: boolean) => void;
    currentPrompt: string;
    setCurrentPrompt: (prompt: string) => void;
    autoClearPrompt: boolean;
    setAutoClearPrompt: (value: boolean) => void;

    // Gallery modal state
    selectedImage: ImageItem | null;
    setSelectedImage: (image: ImageItem | null) => void;
    selectedImageIds: string[];
    setSelectedImageIds: (ids: string[]) => void;
    clearSelectedImageIds: () => void;
    toggleSelectedImageId: (id: string) => void;

    // Gallery settings
    galleryColumns: GalleryColumnSize;
    setGalleryColumns: (cols: GalleryColumnSize) => void;
    galleryDisplayMode: GalleryDisplayMode;
    setGalleryDisplayMode: (mode: GalleryDisplayMode) => void;
    galleryPageSize: number;
    setGalleryPageSize: (value: number) => void;
    deleteLocalFile: boolean;
    setDeleteLocalFile: (value: boolean) => void;
    deleteImportedOriginal: boolean;
    setDeleteImportedOriginal: (value: boolean) => void;
    gallerySelectionColor: string;
    setGallerySelectionColor: (value: string) => void;
    gallerySelectionBoxColor: string;
    setGallerySelectionBoxColor: (value: string) => void;
    galleryTagColor: string;
    setGalleryTagColor: (value: string) => void;

    // Thought images (draft images from Nanobanana2 thinking)
    thoughtImages: ThoughtImage[];
    addThoughtImages: (images: ThoughtImage[]) => void;
    removeThoughtImage: (id: string) => void;
    clearThoughtImages: () => void;
}

export const useStore = create<AppState>()(
    persist(
        (set, get) => ({
            // Images - loaded from backend JSON, NOT persisted to localStorage
            images: [],
            galleryLoaded: false,
            backendCapabilities: {
                backendVersion: 'v1',
                features: {
                    galleryImport: false,
                },
            },

            loadBackendCapabilities: async () => {
                const capabilities = await getBackendCapabilities();
                set({ backendCapabilities: capabilities });
            },

            loadUiSettingsFromServer: async () => {
                try {
                    const settings = await loadBackendSettings();
                    applyUiSettings(set, {
                        ui: {
                            prompt: {
                                autoClear: settings.ui.prompt.autoClear.value,
                            },
                            gallery: {
                                columns: settings.ui.gallery.columns.value,
                                displayMode: settings.ui.gallery.displayMode.value,
                                pageSize: settings.ui.gallery.pageSize.value,
                                deleteLocalFile: settings.ui.gallery.deleteLocalFile.value,
                                deleteImportedOriginal: settings.ui.gallery.deleteImportedOriginal.value,
                                selectionColor: settings.ui.gallery.selectionColor.value,
                                selectionBoxColor: settings.ui.gallery.selectionBoxColor.value,
                                tagColor: settings.ui.gallery.tagColor.value,
                            },
                        },
                    });
                } catch (error) {
                    console.warn('Failed to load UI settings from server:', error);
                }
            },

            resetUiSettingsToDefaults: async () => {
                const settings = await resetBackendSettings();
                applyUiSettings(set, settings);
            },

            loadGalleryFromServer: async () => {
                if (get().galleryLoaded) return;
                try {
                    const data = await loadGallery();
                    const sorted = sortGalleryImages(data.images);
                    set({ images: sorted, allTags: deriveTags(sorted), galleryLoaded: true });
                    console.log(`📦 Loaded ${data.images.length} images from server`);
                } catch (e) {
                    console.warn('Failed to load gallery from server:', e);
                    set({ galleryLoaded: true });
                }
            },

            reloadGalleryFromServer: async () => {
                try {
                    const data = await loadGallery();
                    const sorted = sortGalleryImages(data.images);
                    set({ images: sorted, allTags: deriveTags(sorted), galleryLoaded: true });
                    console.log(`📦 Reloaded ${data.images.length} images from server`);
                } catch (e) {
                    console.warn('Failed to reload gallery from server:', e);
                    set({ galleryLoaded: true });
                }
            },

            addImage: (image) => {
                set((state) => {
                    const images = [image, ...state.images];
                    return { images, allTags: mergeTags(state.allTags, [image]) };
                });
                // Only save to backend if not a loading placeholder
                if (image.status === 'success') {
                    saveToGallery(image).catch(e => console.error('Failed to save image:', e));
                }
            },

            addImagesLocal: (nextImages) => {
                if (nextImages.length === 0) return;
                set((state) => ({
                    images: [...nextImages, ...state.images],
                    allTags: mergeTags(state.allTags, nextImages),
                }));
            },

            addImportedImages: (images) => {
                set((state) => {
                    const existingIds = new Set(state.images.map((img) => img.id));
                    const nextImages = images.filter((img) => !existingIds.has(img.id));
                    const mergedImages = [...nextImages, ...state.images];
                    return {
                        images: mergedImages,
                        allTags: mergeTags(state.allTags, nextImages),
                    };
                });
            },

            removeImagesLocal: (ids) => set((state) => {
                const idSet = new Set(ids);
                const deletedImages = state.images.filter((image) => idSet.has(image.id));
                const images = state.images.filter((image) => !idSet.has(image.id));
                const allTags = deriveTagsAfterDelete(state.allTags, images, deletedImages);
                return {
                    images,
                    allTags,
                    selectedImageIds: state.selectedImageIds.filter((id) => !idSet.has(id)),
                    filters: {
                        ...state.filters,
                        selectedTags: state.filters.selectedTags.filter((tag) => allTags.includes(tag)),
                    },
                };
            }),

            addTagsToImagesLocal: (ids, tags) => set((state) => {
                const idSet = new Set(ids);
                const cleaned = tags.map((tag) => tag.trim()).filter(Boolean);
                const images = state.images.map((image) => {
                    if (!idSet.has(image.id)) return image;
                    return { ...image, tags: Array.from(new Set([...image.tags, ...cleaned])) };
                });
                return { images, allTags: deriveTags(images) };
            }),

            removeTagsFromImagesLocal: (ids, tags) => set((state) => {
                const idSet = new Set(ids);
                const removeSet = new Set(tags.map((tag) => tag.trim()).filter(Boolean));
                const images = state.images.map((image) => {
                    if (!idSet.has(image.id)) return image;
                    return { ...image, tags: image.tags.filter((tag) => !removeSet.has(tag)) };
                });
                const allTags = deriveTags(images);
                return {
                    images,
                    allTags,
                    filters: {
                        ...state.filters,
                        selectedTags: state.filters.selectedTags.filter((tag) => allTags.includes(tag)),
                    },
                };
            }),

            setImagesFavoriteLocal: (ids, favorite) => set((state) => {
                const idSet = new Set(ids);
                return {
                    images: state.images.map((image) => (
                        idSet.has(image.id) ? { ...image, isFavorite: favorite } : image
                    )),
                };
            }),

            updateImage: (id, updates) => {
                console.log(`📝 updateImage called for ${id}`, updates);
                let applied = false;
                set((state) => {
                    if (!state.images.some((img) => img.id === id)) {
                        return state;
                    }
                    applied = true;
                    const images = state.images.map((img) =>
                        img.id === id ? { ...img, ...updates } : img
                    );
                    const allTags = Array.isArray(updates.tags) ? deriveTags(images) : state.allTags;
                    return {
                        images,
                        allTags,
                        filters: Array.isArray(updates.tags)
                            ? {
                                ...state.filters,
                                selectedTags: state.filters.selectedTags.filter((tag) => allTags.includes(tag)),
                            }
                            : state.filters,
                    };
                });
                if (!applied) {
                    return false;
                }
                // Save to backend if updated to success status
                const updated = get().images.find(img => img.id === id);
                if (updated && updated.status === 'success') {
                    console.log(`💾 Saving to gallery: ${id}`);
                    saveToGallery(updated).catch(e => console.error('Failed to update image:', e));
                }
                return true;
            },

            removeImage: (id) => {
                // Check if image was saved to backend before deleting
                const image = get().images.find(img => img.id === id);
                const wasSaved = image?.status === 'success';
                const shouldDeleteLocal = get().deleteLocalFile;

                console.log(`🗑️ removeImage called for ${id}, wasSaved=${wasSaved}, deleteLocal=${shouldDeleteLocal}`);
                set((state) => {
                    const deletedImages = state.images.filter((img) => img.id === id);
                    const images = state.images.filter((img) => img.id !== id);
                    const allTags = deriveTagsAfterDelete(state.allTags, images, deletedImages);
                    return {
                        images,
                        allTags,
                        filters: {
                            ...state.filters,
                            selectedTags: state.filters.selectedTags.filter((tag) =>
                                allTags.includes(tag)
                            ),
                        },
                    };
                });

                if (wasSaved) {
                    deleteFromGallery(id, shouldDeleteLocal).catch(e => console.error('Failed to delete image:', e));
                }
            },

            removeImageLocalOnly: (id) => {
                set((state) => {
                    const deletedImages = state.images.filter((img) => img.id === id);
                    const images = state.images.filter((img) => img.id !== id);
                    const allTags = deriveTagsAfterDelete(state.allTags, images, deletedImages);
                    return {
                        images,
                        allTags,
                        selectedImageIds: state.selectedImageIds.filter((item) => item !== id),
                        filters: {
                            ...state.filters,
                            selectedTags: state.filters.selectedTags.filter((tag) =>
                                allTags.includes(tag)
                            ),
                        },
                    };
                });
            },

            toggleFavorite: (id) => {
                const state = get();
                const updatedImages = state.images.map((img) =>
                    img.id === id ? { ...img, isFavorite: !img.isFavorite } : img
                );
                set({ images: updatedImages });
                // Save updated image to backend
                const updated = updatedImages.find(img => img.id === id);
                if (updated) saveToGallery(updated).catch(e => console.error('Failed to update image:', e));
            },

            addTagToImage: (id, tag) => {
                const state = get();
                const updatedImages = state.images.map((img) =>
                    img.id === id && !img.tags.includes(tag)
                        ? { ...img, tags: [...img.tags, tag] }
                        : img
                );
                set({ images: updatedImages, allTags: deriveTags(updatedImages) });
                const updated = updatedImages.find(img => img.id === id);
                if (updated) saveToGallery(updated).catch(e => console.error('Failed to update image:', e));
            },

            removeTagFromImage: (id, tag) => {
                const state = get();
                const updatedImages = state.images.map((img) =>
                    img.id === id ? { ...img, tags: img.tags.filter((t) => t !== tag) } : img
                );
                const allTags = deriveTags(updatedImages);
                set((state) => ({
                    images: updatedImages,
                    allTags,
                    filters: {
                        ...state.filters,
                        selectedTags: state.filters.selectedTags.filter((selected) => allTags.includes(selected)),
                    },
                }));
                const updated = updatedImages.find(img => img.id === id);
                if (updated) saveToGallery(updated).catch(e => console.error('Failed to update image:', e));

                // Auto-remove from global tags if no image uses this tag anymore
                const stillInUse = updatedImages.some(img => img.tags.includes(tag));
                if (!stillInUse) {
                    get().removeTag(tag);
                }
            },

            // Tags
            allTags: [],
            addTag: (tag) => {
                set((state) => ({
                    allTags: state.allTags.includes(tag) ? state.allTags : [...state.allTags, tag]
                }));
                updateGalleryTags(get().allTags).catch(e => console.error('Failed to update tags:', e));
            },
            removeTag: (tag) => {
                set((state) => ({ allTags: state.allTags.filter((t) => t !== tag) }));
                updateGalleryTags(get().allTags).catch(e => console.error('Failed to update tags:', e));
            },

            // Filters (NOT persisted)
            filters: {
                searchQuery: '',
                selectedDate: null,
                selectedTags: [],
                showFavoritesOnly: false,
            },
            setSearchQuery: (query) => set((state) => ({
                filters: { ...state.filters, searchQuery: query }
            })),
            setSelectedDate: (date) => set((state) => ({
                filters: { ...state.filters, selectedDate: date }
            })),
            setSelectedTags: (tags) => set((state) => ({
                filters: { ...state.filters, selectedTags: tags }
            })),
            toggleFavoritesOnly: () => set((state) => ({
                filters: { ...state.filters, showFavoritesOnly: !state.filters.showFavoritesOnly }
            })),

            // Generation - each API has its own default params
            selectedApi: 'openai',
            setSelectedApi: (api) => set(() => ({
                selectedApi: api,
            })),
            // Shared user preferences that should survive API/model switches.
            sharedGenerateParams: {},
            perApiParams: DEFAULT_PER_API_PARAMS,
            selectedModelByApi: DEFAULT_SELECTED_MODELS,
            perApiModelParams: {
                openai: {
                    [DEFAULT_SELECTED_MODELS.openai]: buildModelSnapshot('openai', DEFAULT_SELECTED_MODELS.openai),
                },
                cliproxy: {
                    [DEFAULT_SELECTED_MODELS.cliproxy]: buildModelSnapshot('cliproxy', DEFAULT_SELECTED_MODELS.cliproxy),
                },
                nanobanana2: {
                    [DEFAULT_SELECTED_MODELS.nanobanana2]: buildModelSnapshot('nanobanana2', DEFAULT_SELECTED_MODELS.nanobanana2),
                },
                apimart: {
                    [DEFAULT_SELECTED_MODELS.apimart]: buildModelSnapshot('apimart', DEFAULT_SELECTED_MODELS.apimart),
                },
                sousaku: {
                    [DEFAULT_SELECTED_MODELS.sousaku]: buildModelSnapshot('sousaku', DEFAULT_SELECTED_MODELS.sousaku),
                },
            },
            setSelectedModel: (model) => set((state) => {
                const api = state.selectedApi;
                const currentModels = state.perApiModelParams?.[api] || {};
                const nextSnapshot = currentModels[model] || buildModelSnapshot(api, model, mergeGenerateParams(api, model, state));
                const nextPerApiModelParams = {
                    ...state.perApiModelParams,
                    [api]: {
                        ...currentModels,
                        [model]: nextSnapshot,
                    },
                };
                const nextPerApiParams = {
                    ...state.perApiParams,
                    [api]: nextSnapshot,
                };
                return {
                    selectedModelByApi: {
                        ...state.selectedModelByApi,
                        [api]: model,
                    },
                    perApiModelParams: nextPerApiModelParams,
                    perApiParams: nextPerApiParams,
                };
            }),
            // generateParams is computed via selector: useGenerateParams()
            setGenerateParams: (params) => set((state) => {
                const nextParams = normalizeGenerateParams(params);
                return {
                // Keep common knobs globally stable, but still remember the current model snapshot.
                sharedGenerateParams: {
                    ...state.sharedGenerateParams,
                    ...pickSharedGenerateParams(nextParams),
                },
                perApiModelParams: (() => {
                    const api = state.selectedApi;
                    const model = resolveSelectedModel(api, state);
                    const currentModels = state.perApiModelParams?.[api] || {};
                    const baseSnapshot = currentModels[model] || buildModelSnapshot(api, model, state.perApiParams?.[api]);
                    const nextSnapshot = normalizeGenerateParams({
                        ...baseSnapshot,
                        ...nextParams,
                    });
                    if (api === 'apimart') nextSnapshot.apimartModel = model;
                    if (api === 'cliproxy') nextSnapshot.cliproxyModel = model;
                    if (api === 'sousaku') nextSnapshot.sousakuModel = model;
                    return {
                        ...state.perApiModelParams,
                        [api]: {
                            ...currentModels,
                            [model]: nextSnapshot,
                        },
                    };
                })(),
                perApiParams: (() => {
                    const api = state.selectedApi;
                    const model = resolveSelectedModel(api, state);
                    const currentModels = state.perApiModelParams?.[api] || {};
                    const baseSnapshot = currentModels[model] || buildModelSnapshot(api, model, state.perApiParams?.[api]);
                    const nextSnapshot = normalizeGenerateParams({
                        ...baseSnapshot,
                        ...nextParams,
                    });
                    if (api === 'apimart') nextSnapshot.apimartModel = model;
                    if (api === 'cliproxy') nextSnapshot.cliproxyModel = model;
                    if (api === 'sousaku') nextSnapshot.sousakuModel = model;
                    return {
                        ...state.perApiParams,
                        [api]: nextSnapshot,
                    };
                })(),
                };
            }),

            // Uploads (NOT persisted)
            uploadedImages: [],
            selectedReferenceIds: [],
            addUploadedImage: (image) => set((state) => ({
                uploadedImages: [...state.uploadedImages, image]
            })),
            updateUploadedImage: (id, updates) => set((state) => ({
                uploadedImages: state.uploadedImages.map((img) => (
                    img.id === id ? { ...img, ...updates } : img
                )),
            })),
            removeUploadedImage: async (id) => {
                const snapshot = get();
                const removedImage = snapshot.uploadedImages.find((img) => img.id === id);
                const wasSelected = snapshot.selectedReferenceIds.includes(id);

                // Also clean up mask data when removing an uploaded image
                set((state) => {
                    const { [id]: _m, ...restMask } = state.maskData;
                    const { [id]: _f, ...restFeather } = state.maskFeather;
                    return {
                        uploadedImages: state.uploadedImages.filter((img) => img.id !== id),
                        selectedReferenceIds: state.selectedReferenceIds.filter((item) => item !== id),
                        maskData: restMask,
                        maskFeather: restFeather,
                    };
                });

                if (!removedImage?.refId) return;

                try {
                    await deleteReferenceImage(removedImage.refId);
                } catch (deleteError) {
                    set((state) => {
                        if (state.uploadedImages.some((img) => img.id === id)) return {};
                        return {
                            uploadedImages: [...state.uploadedImages, removedImage],
                            selectedReferenceIds: wasSelected
                                ? Array.from(new Set([...state.selectedReferenceIds, id]))
                                : state.selectedReferenceIds,
                        };
                    });
                    throw deleteError;
                }
            },
            clearUploadedImages: () => set({ uploadedImages: [], selectedReferenceIds: [], maskData: {}, maskFeather: {} }),
            loadReferenceImagesFromServer: async (options = {}) => {
                try {
                    const limit = options.limit ?? 120;
                    const offset = options.reset ? 0 : get().uploadedImages.filter((img) => img.refId).length;
                    const { items: references, total } = await loadReferenceImages(limit, offset);
                    set((state) => {
                        const nextImages = state.uploadedImages.map((img) => ({ ...img }));
                        for (const ref of references) {
                            const restored = {
                                id: ref.ref_id,
                                name: ref.name,
                                preview: ref.preview_url || ref.local_url,
                                base64: ref.local_url,
                                refId: ref.ref_id,
                                localUrl: ref.local_url,
                                publicUrl: ref.public_urls?.apimart?.url,
                                contentType: ref.content_type,
                                size: ref.size,
                                status: 'ready' as const,
                            };
                            const existingIndex = nextImages.findIndex((img) => (
                                img.refId === ref.ref_id || img.id === ref.ref_id
                            ));
                            if (existingIndex >= 0) {
                                nextImages[existingIndex] = {
                                    ...nextImages[existingIndex],
                                    ...restored,
                                    id: nextImages[existingIndex].id,
                                };
                            } else {
                                nextImages.push(restored);
                            }
                        }
                        const readyIds = new Set(
                            nextImages
                                .filter((img) => Boolean(img.refId || img.localUrl || img.publicUrl || img.base64) && (img.status || 'ready') === 'ready')
                                .map((img) => img.id),
                        );
                        return {
                            uploadedImages: nextImages,
                            selectedReferenceIds: state.selectedReferenceIds.filter((id) => readyIds.has(id)),
                        };
                    });
                    return { loaded: get().uploadedImages.filter((img) => img.refId).length, total };
                } catch (error) {
                    console.error('Failed to load reference library:', error);
                    return { loaded: get().uploadedImages.filter((img) => img.refId).length, total: 0 };
                }
            },
            uploadReferenceImages: async (files) => {
                const fileArray = files.filter((file) => file.type.startsWith('image/'));
                for (const file of fileArray) {
                    const id = crypto.randomUUID();
                    const preview = URL.createObjectURL(file);
                    set((state) => ({
                        uploadedImages: [
                            ...state.uploadedImages,
                            {
                                id,
                                file,
                                name: file.name,
                                preview,
                                status: 'uploading',
                            },
                        ],
                    }));

                    try {
                        const result = await uploadReferenceImage(file);
                        set((state) => ({
                            uploadedImages: state.uploadedImages.map((img) => (
                                img.id === id
                                    ? {
                                        ...img,
                                        base64: result.local_url,
                                        refId: result.ref_id,
                                        localUrl: result.local_url,
                                        publicUrl: result.public_urls?.apimart?.url,
                                        contentType: result.content_type,
                                        size: result.size,
                                        preview: result.preview_url || result.local_url,
                                        status: 'ready' as const,
                                        error: undefined,
                                    }
                                    : img
                            )),
                            selectedReferenceIds: Array.from(new Set([...state.selectedReferenceIds, id])),
                        }));
                    } catch (uploadError) {
                        console.error('Upload to hosting failed:', uploadError);
                        set((state) => ({
                            uploadedImages: state.uploadedImages.map((img) => (
                                img.id === id
                                    ? {
                                        ...img,
                                        status: 'failed' as const,
                                        error: uploadError instanceof Error ? uploadError.message : '图片上传失败，请重新上传。',
                                    }
                                    : img
                            )),
                            selectedReferenceIds: state.selectedReferenceIds.filter((item) => item !== id),
                        }));
                    }
                }
            },
            addReferenceUrl: (url) => {
                const id = crypto.randomUUID();
                set((state) => ({
                    uploadedImages: [
                        ...state.uploadedImages,
                        {
                            id,
                            name: 'remote-image',
                            preview: url,
                            base64: url,
                            status: 'uploading',
                        },
                    ],
                }));
                importReferenceImageUrl(url)
                    .then((result) => {
                        set((state) => ({
                            uploadedImages: state.uploadedImages.map((img) => (
                                img.id === id
                                    ? {
                                        ...img,
                                        id: result.ref_id,
                                        name: result.name || 'remote-image',
                                        preview: result.preview_url || result.local_url,
                                        base64: result.local_url,
                                        refId: result.ref_id,
                                        localUrl: result.local_url,
                                        publicUrl: result.public_urls?.apimart?.url,
                                        contentType: result.content_type,
                                        size: result.size,
                                        status: 'ready' as const,
                                        error: undefined,
                                    }
                                    : img
                            )),
                            selectedReferenceIds: Array.from(new Set([
                                ...state.selectedReferenceIds.filter((item) => item !== id),
                                result.ref_id,
                            ])),
                        }));
                    })
                    .catch((error) => {
                        set((state) => ({
                            uploadedImages: state.uploadedImages.map((img) => (
                                img.id === id
                                    ? {
                                        ...img,
                                        status: 'failed' as const,
                                        error: error instanceof Error ? error.message : '远程参考图导入失败。',
                                    }
                                    : img
                            )),
                        }));
                    });
            },
            setSelectedReferenceIds: (ids) => set((state) => {
                const readyIds = new Set(
                    state.uploadedImages
                        .filter((img) => Boolean(img.refId || img.localUrl || img.publicUrl || img.base64) && (img.status || 'ready') === 'ready')
                        .map((img) => img.id),
                );
                return { selectedReferenceIds: Array.from(new Set(ids.filter((id) => readyIds.has(id)))) };
            }),
            clearSelectedReferenceIds: () => set({ selectedReferenceIds: [] }),
            toggleSelectedReferenceId: (id) => set((state) => {
                const image = state.uploadedImages.find((img) => img.id === id);
                if (!image || !Boolean(image.refId || image.localUrl || image.publicUrl || image.base64) || (image.status || 'ready') !== 'ready') {
                    return {};
                }
                const exists = state.selectedReferenceIds.includes(id);
                return {
                    selectedReferenceIds: exists
                        ? state.selectedReferenceIds.filter((item) => item !== id)
                        : [...state.selectedReferenceIds, id],
                };
            }),

            // Mask data (NOT persisted — lives only in memory)
            maskData: {},
            maskFeather: {},
            setMaskData: (imageId, dataUrl, feather) => set((state) => ({
                maskData: { ...state.maskData, [imageId]: dataUrl },
                maskFeather: { ...state.maskFeather, [imageId]: feather ?? 0 },
            })),
            removeMaskData: (imageId) => set((state) => {
                const { [imageId]: _m, ...restMask } = state.maskData;
                const { [imageId]: _f, ...restFeather } = state.maskFeather;
                return { maskData: restMask, maskFeather: restFeather };
            }),
            clearMaskData: () => set({ maskData: {}, maskFeather: {} }),

            // UI
            isGenerating: false,
            setIsGenerating: (value) => set({ isGenerating: value }),
            currentPrompt: '',
            setCurrentPrompt: (prompt) => set({ currentPrompt: prompt }),
            autoClearPrompt: false,
            setAutoClearPrompt: (value) => {
                set({ autoClearPrompt: value });
                saveBackendSettings({ ui: { prompt: { autoClear: value } } }).catch(e => console.error('Failed to save setting:', e));
            },

            // Gallery modal
            selectedImage: null,
            setSelectedImage: (image) => set({ selectedImage: image }),
            selectedImageIds: [],
            setSelectedImageIds: (ids) => set({ selectedImageIds: Array.from(new Set(ids)) }),
            clearSelectedImageIds: () => set({ selectedImageIds: [] }),
            toggleSelectedImageId: (id) => set((state) => {
                const exists = state.selectedImageIds.includes(id);
                return {
                    selectedImageIds: exists
                        ? state.selectedImageIds.filter((item) => item !== id)
                        : [...state.selectedImageIds, id],
                };
            }),

            // Gallery settings
            galleryColumns: 5,
            setGalleryColumns: (cols) => {
                set({ galleryColumns: cols });
                saveBackendSettings({ ui: { gallery: { columns: cols } } }).catch(e => console.error('Failed to save setting:', e));
            },
            galleryDisplayMode: 'waterfall',
            setGalleryDisplayMode: (mode) => {
                set({ galleryDisplayMode: mode });
                saveBackendSettings({ ui: { gallery: { displayMode: mode } } }).catch(e => console.error('Failed to save setting:', e));
            },
            galleryPageSize: DEFAULT_GALLERY_PAGE_SIZE,
            setGalleryPageSize: (value) => {
                const nextValue = clampGalleryPageSize(value);
                set({ galleryPageSize: nextValue });
                saveBackendSettings({ ui: { gallery: { pageSize: nextValue } } }).catch(e => console.error('Failed to save setting:', e));
            },
            deleteLocalFile: false,
            setDeleteLocalFile: (value) => {
                set({ deleteLocalFile: value });
                saveBackendSettings({ ui: { gallery: { deleteLocalFile: value } } }).catch(e => console.error('Failed to save setting:', e));
            },
            deleteImportedOriginal: false,
            setDeleteImportedOriginal: (value) => {
                set({ deleteImportedOriginal: value });
                saveBackendSettings({ ui: { gallery: { deleteImportedOriginal: value } } }).catch(e => console.error('Failed to save setting:', e));
            },
            gallerySelectionColor: DEFAULT_GALLERY_SELECTION_COLOR,
            setGallerySelectionColor: (value) => {
                set({ gallerySelectionColor: value });
                saveBackendSettings({ ui: { gallery: { selectionColor: value } } }).catch(e => console.error('Failed to save setting:', e));
            },
            gallerySelectionBoxColor: DEFAULT_GALLERY_SELECTION_BOX_COLOR,
            setGallerySelectionBoxColor: (value) => {
                set({ gallerySelectionBoxColor: value });
                saveBackendSettings({ ui: { gallery: { selectionBoxColor: value } } }).catch(e => console.error('Failed to save setting:', e));
            },
            galleryTagColor: DEFAULT_GALLERY_TAG_COLOR,
            setGalleryTagColor: (value) => {
                set({ galleryTagColor: value });
                saveBackendSettings({ ui: { gallery: { tagColor: value } } }).catch(e => console.error('Failed to save setting:', e));
            },

            // Thought images (NOT persisted)
            thoughtImages: [],
            addThoughtImages: (images) => set((state) => ({
                thoughtImages: [...state.thoughtImages, ...images]
            })),
            removeThoughtImage: (id) => set((state) => ({
                thoughtImages: state.thoughtImages.filter((img) => img.id !== id)
            })),
            clearThoughtImages: () => set({ thoughtImages: [] }),
        }),
        {
            name: 'apimart-v2-storage',
            // Only persist settings, NOT images (they're in backend JSON now)
            partialize: (state) => ({
                selectedApi: state.selectedApi,
                sharedGenerateParams: state.sharedGenerateParams,
                perApiParams: state.perApiParams,
                selectedModelByApi: state.selectedModelByApi,
                perApiModelParams: state.perApiModelParams,
            }),
        }
    )
);

// Selector hook: returns generateParams for the currently selected API
export const useGenerateParams = () =>
    useStore(useShallow((s) => {
        const api = s.selectedApi;
        const model = resolveSelectedModel(api, s);
        // Shared params stay consistent across API/model switches; model snapshot fills provider-specific fields.
        return mergeGenerateParams(api, model, s);
    }));
