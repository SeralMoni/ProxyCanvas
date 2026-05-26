import CssMasonry from 'react-masonry-css';
import {
    useContainerPosition,
    useMasonry,
    usePositioner,
    useResizeObserver,
    useScroller,
} from 'masonic';
import { useStore } from '../../store';
import { useMemo, useCallback, useState, useRef, useEffect, memo } from 'react';
import type { ComponentType, CSSProperties, MouseEvent as ReactMouseEvent } from 'react';
import type { RenderComponentProps } from 'masonic';
import { motion } from 'framer-motion';
import { Star, Trash2, Loader2 } from 'lucide-react';
import { format } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import { ImageModal } from './ImageModal';
import type { ImageItem } from '../../types';
import { colorWithAlpha, normalizeHexColor } from '../../utils/color';
import {
    batchDeleteGalleryImages,
    batchExportGalleryImages,
    batchFavoriteGalleryImages,
    batchUpdateGalleryTags,
} from '../../services/api';
import { useProviders } from '../../hooks/useProviders';
import { providerBadgeClass, providerBadgeStyle, providerLabel } from '../../utils/providers';
import { normalizePromptText } from '../../utils/prompt';

const INITIAL_LOAD = 60;
const FALLBACK_SELECTION_COLOR = '#fdba74';
const FALLBACK_SELECTION_BOX_COLOR = '#fef08a';
const FALLBACK_TAG_COLOR = '#f43f5e';
const VIRTUAL_MASONRY_COLUMN_WIDTH = 220;
const VIRTUAL_MASONRY_GUTTER = 12;
const VIRTUAL_MASONRY_ITEM_HEIGHT_ESTIMATE = 260;
const VIRTUAL_MASONRY_OVERSCAN = 1.5;
const VIRTUAL_MASONRY_SCROLL_FPS = 12;

function normalizeSearchText(value: unknown) {
    return String(value || '')
        .normalize('NFKC')
        .toLowerCase()
        .replace(/\\r\\n|\\n|\\r/g, ' ')
        .replace(/[\r\n\t]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function queryFromRelativePath(image: ImageItem) {
    if (!image.relativePath) return '';
    return `path=${encodeURIComponent(image.relativePath)}`;
}

function queryFromServeUrl(value?: string) {
    if (!value || !value.startsWith('/api/serve-image')) return '';
    try {
        const url = new URL(value, window.location.origin);
        const path = url.searchParams.get('path');
        if (!path) return '';
        return `path=${encodeURIComponent(path)}`;
    } catch {
        return '';
    }
}

function galleryThumbnailSrc(image: ImageItem) {
    const storageQuery =
        queryFromRelativePath(image) ||
        queryFromServeUrl(image.localPath) ||
        queryFromServeUrl(image.thumbnail);

    if (storageQuery) {
        return `/api/thumbnail?${storageQuery}&w=512`;
    }

    if (image.savedFilePath) {
        return `/api/thumbnail?path=${encodeURIComponent(image.savedFilePath)}&w=512`;
    }

    return image.thumbnail || image.localPath || '';
}

function galleryOriginalSrc(image: ImageItem) {
    const storageQuery =
        queryFromRelativePath(image) ||
        queryFromServeUrl(image.localPath) ||
        queryFromServeUrl(image.thumbnail);

    if (storageQuery) {
        return `/api/serve-image?${storageQuery}`;
    }

    if (image.savedFilePath) {
        return `/api/serve-image?path=${encodeURIComponent(image.savedFilePath)}`;
    }

    return image.thumbnail || image.localPath || '';
}

function selectedCardStyle(color: string) {
    const safeColor = normalizeHexColor(color || FALLBACK_SELECTION_COLOR, FALLBACK_SELECTION_COLOR);
    return {
        borderColor: safeColor,
        boxShadow: [
            `0 0 0 1px ${colorWithAlpha(safeColor, 0.88, FALLBACK_SELECTION_COLOR)}`,
            `0 0 22px ${colorWithAlpha(safeColor, 0.34, FALLBACK_SELECTION_COLOR)}`,
        ].join(','),
    };
}

function tagStyle(color: string) {
    const safeColor = normalizeHexColor(color || FALLBACK_TAG_COLOR, FALLBACK_TAG_COLOR);
    return {
        backgroundColor: colorWithAlpha(safeColor, 0.82, FALLBACK_TAG_COLOR),
        boxShadow: `0 0 0 1px ${colorWithAlpha(safeColor, 0.34, FALLBACK_TAG_COLOR)}`,
    };
}

function selectionBoxStyle(box: SelectionBox, color: string) {
    const safeColor = normalizeHexColor(color || FALLBACK_SELECTION_BOX_COLOR, FALLBACK_SELECTION_BOX_COLOR);
    return {
        ...rectFromPoints(box),
        borderColor: colorWithAlpha(safeColor, 0.8, FALLBACK_SELECTION_BOX_COLOR),
        backgroundColor: colorWithAlpha(safeColor, 0.08, FALLBACK_SELECTION_BOX_COLOR),
        boxShadow: `0 0 0 1px ${colorWithAlpha(safeColor, 0.2, FALLBACK_SELECTION_BOX_COLOR)}`,
    };
}

// ─── Memoized Gallery Card ─────────────────────────────────────

interface GalleryCardProps {
    image: ImageItem;
    isSelected: boolean;
    layout?: 'css-masonry' | 'virtual-masonry';
    selectionColor: string;
    tagColor: string;
    providerName: string;
    providerBadge: string;
    providerBadgeStyle?: CSSProperties;
    onSelect: (image: ImageItem, event: ReactMouseEvent) => void;
    onContextMenu: (image: ImageItem, event: ReactMouseEvent) => void;
    registerCard: (id: string, node: HTMLDivElement | null) => void;
}

const GalleryCard = memo(function GalleryCard({ image, isSelected, layout = 'css-masonry', selectionColor, tagColor, providerName, providerBadge, providerBadgeStyle, onSelect, onContextMenu, registerCard }: GalleryCardProps) {
    const toggleFavorite = useStore((s) => s.toggleFavorite);
    const removeImage = useStore((s) => s.removeImage);
    const deleteLocalFile = useStore((s) => s.deleteLocalFile);
    const hasImageSize = Boolean(image.width && image.height && image.width > 0 && image.height > 0);
    const imageFrameStyle: CSSProperties | undefined = hasImageSize
        ? { aspectRatio: `${image.width} / ${image.height}` }
        : undefined;
    const cardClassName = layout === 'virtual-masonry'
        ? 'group cursor-pointer'
        : 'mb-3 group cursor-pointer';
    const useEntryAnimation = layout !== 'virtual-masonry';

    return (
        <motion.div
            data-gallery-card="true"
            ref={(node) => registerCard(image.id, node)}
            initial={useEntryAnimation ? { opacity: 0, y: -15 } : false}
            animate={useEntryAnimation ? { opacity: 1, y: 0 } : undefined}
            transition={useEntryAnimation ? {
                duration: 0.35,
                ease: 'easeOut',
            } : undefined}
            className={cardClassName}
            onClick={(event) => onSelect(image, event)}
            onContextMenu={(event) => onContextMenu(image, event)}
            onDragStart={(event) => event.preventDefault()}
        >
            <div className={`relative overflow-hidden rounded-[3px] bg-[var(--bg-card)] border transition-all duration-300 hover:-translate-y-1 ${
                isSelected
                    ? 'border-transparent'
                    : 'border-[var(--border-subtle)] hover:border-sky-300/45 hover:shadow-[0_10px_24px_rgba(0,0,0,0.22)]'
            }`} style={isSelected ? selectedCardStyle(selectionColor) : undefined}>
                {/* Image or Loading Placeholder */}
                <div
                    className={hasImageSize ? 'w-full overflow-hidden' : 'aspect-auto min-h-[120px]'}
                    style={imageFrameStyle}
                >
                    {image.status === 'loading' ? (
                        <div className="w-full h-48 flex flex-col items-center justify-center bg-[var(--bg-secondary)] gap-3">
                            <Loader2 className="w-8 h-8 animate-spin text-[var(--accent-primary)]" />
                            <span className="text-xs text-[var(--text-muted)]">生成中...</span>
                        </div>
                    ) : (
                        <img
                            src={galleryThumbnailSrc(image)}
                            alt={image.prompt}
                            width={hasImageSize ? image.width : undefined}
                            height={hasImageSize ? image.height : undefined}
                            className={hasImageSize ? 'w-full h-full object-cover' : 'w-full h-auto object-cover'}
                            loading="lazy"
                            decoding="async"
                            draggable={false}
                            onDragStart={(event) => event.preventDefault()}
                            onError={(event) => {
                                const target = event.currentTarget;
                                if (target.dataset.fallback === '1') return;
                                target.dataset.fallback = '1';
                                target.src = galleryOriginalSrc(image);
                            }}
                        />
                    )}
                </div>

                {/* Overlay on hover */}
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                    <div className="absolute bottom-0 left-0 right-0 p-4">
                        <p className="text-white text-sm line-clamp-2 mb-2">
                            {image.prompt}
                        </p>
                        <div className="flex items-center justify-between">
                            <span className="text-white/60 text-xs">
                                {format(new Date(image.createdAt), 'M月d日 HH:mm', { locale: zhCN })}
                            </span>
                            <span
                                className={`text-xs px-2 py-0.5 rounded ${providerBadge}`}
                                style={providerBadgeStyle}
                            >
                                {providerName}
                            </span>
                        </div>
                    </div>
                </div>

                {/* Quick actions */}
                <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            toggleFavorite(image.id);
                        }}
                        className={`p-1.5 rounded-lg backdrop-blur-sm transition-colors ${image.isFavorite
                            ? 'bg-yellow-500 text-white'
                            : 'bg-black/50 text-white hover:bg-yellow-500'
                            }`}
                        title="收藏"
                    >
                        <Star className={`w-4 h-4 ${image.isFavorite ? 'fill-current' : ''}`} />
                    </button>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            removeImage(image.id);
                        }}
                        className="p-1.5 rounded-lg backdrop-blur-sm bg-black/50 text-white hover:bg-red-500 transition-colors"
                        title={deleteLocalFile ? '删除（同时删除本地文件）' : '从画廊移除（不删除本地文件）'}
                    >
                        <Trash2 className="w-4 h-4" />
                    </button>
                </div>

                {/* Tags */}
                {image.tags.length > 0 && (
                    <div className="absolute top-2 left-2 flex flex-wrap gap-1 max-w-[70%]">
                        {image.tags.slice(0, 2).map((tag) => (
                            <span
                                key={tag}
                                className="px-2 py-0.5 rounded-full text-xs text-white backdrop-blur-sm"
                                style={tagStyle(tagColor)}
                            >
                                {tag}
                            </span>
                        ))}
                        {image.tags.length > 2 && (
                            <span className="px-2 py-0.5 rounded-full text-xs bg-black/50 text-white backdrop-blur-sm">
                                +{image.tags.length - 2}
                            </span>
                        )}
                    </div>
                )}
            </div>
        </motion.div>
    );
});

interface SelectionBox {
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
}

interface ContextMenuState {
    x: number;
    y: number;
    ids: string[];
}

function rectFromPoints(box: SelectionBox) {
    const left = Math.min(box.startX, box.currentX);
    const top = Math.min(box.startY, box.currentY);
    const right = Math.max(box.startX, box.currentX);
    const bottom = Math.max(box.startY, box.currentY);
    return { left, top, right, bottom, width: right - left, height: bottom - top };
}

function intersectsRect(a: ReturnType<typeof rectFromPoints>, b: DOMRect) {
    return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
}

function isInteractiveTarget(target: EventTarget | null) {
    return target instanceof HTMLElement && Boolean(target.closest('button,input,textarea,select,a,[data-no-selection="true"]'));
}

function isGalleryCardTarget(target: EventTarget | null) {
    return target instanceof HTMLElement && Boolean(target.closest('[data-gallery-card="true"]'));
}

function GalleryContextMenu({
    state,
    count,
    allFavorite,
    onClose,
    onReusePrompt,
    onDelete,
    onExport,
    onAddTag,
    onRemoveTag,
    onFavorite,
}: {
    state: ContextMenuState;
    count: number;
    allFavorite: boolean;
    onClose: () => void;
    onReusePrompt?: () => void;
    onDelete: () => void;
    onExport: () => void;
    onAddTag: () => void;
    onRemoveTag: () => void;
    onFavorite: () => void;
}) {
    useEffect(() => {
        const close = () => onClose();
        window.addEventListener('click', close);
        window.addEventListener('scroll', close, true);
        return () => {
            window.removeEventListener('click', close);
            window.removeEventListener('scroll', close, true);
        };
    }, [onClose]);

    const itemClass = 'w-full px-3 py-2 text-left text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]';
    const menuWidth = 224;
    const menuHeight = 244;
    const left = Math.min(state.x, Math.max(12, window.innerWidth - menuWidth - 12));
    const top = Math.min(state.y, Math.max(12, window.innerHeight - menuHeight - 12));

    return (
        <div
            data-no-selection="true"
            className="fixed z-[80] w-56 overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[rgba(24,24,27,0.96)] py-1 shadow-[0_18px_48px_rgba(0,0,0,0.45)] backdrop-blur-xl"
            style={{ left, top }}
            onContextMenu={(event) => event.preventDefault()}
            onClick={(event) => event.stopPropagation()}
            onMouseDown={(event) => event.stopPropagation()}
        >
            <div className="border-b border-[var(--border-subtle)] px-3 py-2 text-xs text-[var(--text-muted)]">
                已选 {count} 张
            </div>
            {count === 1 && onReusePrompt && (
                <button className={itemClass} onClick={onReusePrompt}>复用提示词</button>
            )}
            <button className={itemClass} onClick={onExport}>另存为...</button>
            <button className={itemClass} onClick={onAddTag}>添加标签</button>
            <button className={itemClass} onClick={onRemoveTag}>移除标签</button>
            <button className={itemClass} onClick={onFavorite}>{allFavorite ? '取消收藏' : '收藏'}</button>
            <div className="my-1 border-t border-[var(--border-subtle)]" />
            <button className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-red-500/10" onClick={onDelete}>
                删除
            </button>
        </div>
    );
}

function parseTagInput(value: string | null) {
    if (!value) return [];
    return Array.from(new Set(
        value
            .split(/[,，\n]/)
            .map((tag) => tag.trim())
            .filter(Boolean)
    ));
}

function galleryLayoutSignature(images: ImageItem[]) {
    let hash = 2166136261;
    for (const image of images) {
        for (let index = 0; index < image.id.length; index++) {
            hash ^= image.id.charCodeAt(index);
            hash = Math.imul(hash, 16777619);
        }
        hash ^= 124;
        hash = Math.imul(hash, 16777619);
    }
    return `${images.length}:${hash >>> 0}`;
}

function useWindowSize() {
    const getSize = () => ({
        width: window.innerWidth || document.documentElement.clientWidth || 0,
        height: window.innerHeight || document.documentElement.clientHeight || 0,
    });
    const [size, setSize] = useState(getSize);

    useEffect(() => {
        const handleResize = () => setSize(getSize());
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    return size;
}

interface VirtualGalleryMasonryProps {
    images: ImageItem[];
    columns: number;
    layoutSignature: string;
    renderCard: ComponentType<RenderComponentProps<ImageItem>>;
}

function VirtualGalleryMasonry({ images, columns, layoutSignature, renderCard }: VirtualGalleryMasonryProps) {
    const containerRef = useRef<HTMLElement | null>(null);
    const windowSize = useWindowSize();
    const containerPosition = useContainerPosition(containerRef, [windowSize.width, windowSize.height, columns]);
    const positioner = usePositioner({
        width: containerPosition.width || windowSize.width,
        columnWidth: VIRTUAL_MASONRY_COLUMN_WIDTH,
        columnGutter: VIRTUAL_MASONRY_GUTTER,
        rowGutter: VIRTUAL_MASONRY_GUTTER,
        maxColumnCount: columns,
    }, [layoutSignature]);
    const resizeObserver = useResizeObserver(positioner);
    const { scrollTop, isScrolling } = useScroller(containerPosition.offset, VIRTUAL_MASONRY_SCROLL_FPS);

    return useMasonry<ImageItem>({
        positioner,
        resizeObserver,
        items: images,
        render: renderCard,
        itemKey: (image) => image.id,
        itemHeightEstimate: VIRTUAL_MASONRY_ITEM_HEIGHT_ESTIMATE,
        overscanBy: VIRTUAL_MASONRY_OVERSCAN,
        height: windowSize.height,
        scrollTop,
        isScrolling,
        containerRef,
        tabIndex: -1,
    });
}

// ─── Gallery Component ──────────────────────────────────────────
export function Gallery() {
    const images = useStore((s) => s.images);
    const galleryLoaded = useStore((s) => s.galleryLoaded);
    const filters = useStore((s) => s.filters);
    const selectedImage = useStore((s) => s.selectedImage);
    const setSelectedImage = useStore((s) => s.setSelectedImage);
    const selectedImageIds = useStore((s) => s.selectedImageIds);
    const setSelectedImageIds = useStore((s) => s.setSelectedImageIds);
    const clearSelectedImageIds = useStore((s) => s.clearSelectedImageIds);
    const toggleSelectedImageId = useStore((s) => s.toggleSelectedImageId);
    const removeImagesLocal = useStore((s) => s.removeImagesLocal);
    const reloadGalleryFromServer = useStore((s) => s.reloadGalleryFromServer);
    const addTagsToImagesLocal = useStore((s) => s.addTagsToImagesLocal);
    const removeTagsFromImagesLocal = useStore((s) => s.removeTagsFromImagesLocal);
    const setImagesFavoriteLocal = useStore((s) => s.setImagesFavoriteLocal);
    const setCurrentPrompt = useStore((s) => s.setCurrentPrompt);
    const galleryColumns = useStore((s) => s.galleryColumns);
    const galleryDisplayMode = useStore((s) => s.galleryDisplayMode);
    const galleryPageSize = useStore((s) => s.galleryPageSize);
    const deleteLocalFile = useStore((s) => s.deleteLocalFile);
    const gallerySelectionColor = useStore((s) => s.gallerySelectionColor);
    const gallerySelectionBoxColor = useStore((s) => s.gallerySelectionBoxColor);
    const galleryTagColor = useStore((s) => s.galleryTagColor);
    const { providers } = useProviders();
    const selectedImageIdSet = useMemo(() => new Set(selectedImageIds), [selectedImageIds]);

    // Compute masonry breakpoints from gallery column setting
    const masonryBreakpoints = useMemo(() => ({
        default: galleryColumns,
        1536: galleryColumns,
        1280: galleryColumns,
        1024: Math.min(galleryColumns, 3),
        768: 2,
        640: 1,
    }), [galleryColumns]);

    const [currentPage, setCurrentPage] = useState(1);
    const [pageInput, setPageInput] = useState('1');
    const [selectionBox, setSelectionBox] = useState<SelectionBox | null>(null);
    const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
    const [isBatchBusy, setIsBatchBusy] = useState(false);
    const cardRefs = useRef(new Map<string, HTMLDivElement>());
    const selectionStartRef = useRef<{ x: number; y: number; additive: boolean } | null>(null);
    const isSelectingRef = useRef(false);
    const suppressNextClickRef = useRef(false);

    useEffect(() => {
        window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    }, []);

    // Filter images based on current filters
    const filteredImages = useMemo(() => {
        const query = normalizeSearchText(filters.searchQuery);
        return images.filter((img) => {
            if (query) {
                const prompt = normalizeSearchText(img.prompt);
                if (!prompt.includes(query)) {
                    return false;
                }
            }
            if (filters.selectedDate) {
                const imgDate = format(new Date(img.createdAt), 'yyyy-MM-dd');
                const filterDate = format(filters.selectedDate, 'yyyy-MM-dd');
                if (imgDate !== filterDate) {
                    return false;
                }
            }
            if (filters.selectedTags.length > 0) {
                if (!filters.selectedTags.some((tag) => img.tags.includes(tag))) {
                    return false;
                }
            }
            if (filters.showFavoritesOnly && !img.isFavorite) {
                return false;
            }
            return true;
        });
    }, [images, filters]);
    const virtualMasonrySignature = useMemo(
        () => galleryLayoutSignature(filteredImages),
        [filteredImages]
    );

    const pageSize = Math.max(1, Math.floor(galleryPageSize || INITIAL_LOAD));
    const pageCount = Math.max(1, Math.ceil(filteredImages.length / pageSize));
    const safeCurrentPage = Math.min(currentPage, pageCount);

    const paginatedImages = useMemo(() => {
        const start = (safeCurrentPage - 1) * pageSize;
        return filteredImages.slice(start, start + pageSize);
    }, [filteredImages, pageSize, safeCurrentPage]);

    // Reset pagination when filters or display mode change
    useEffect(() => {
        setCurrentPage(1);
    }, [filters, galleryDisplayMode, pageSize]);

    useEffect(() => {
        if (currentPage > pageCount) {
            setCurrentPage(pageCount);
        }
    }, [currentPage, pageCount]);

    useEffect(() => {
        setPageInput(String(safeCurrentPage));
    }, [safeCurrentPage]);

    const commitPageInput = useCallback(() => {
        const parsed = Number.parseInt(pageInput, 10);
        const nextPage = Number.isFinite(parsed)
            ? Math.min(pageCount, Math.max(1, parsed))
            : safeCurrentPage;
        setCurrentPage(nextPage);
        setPageInput(String(nextPage));
    }, [pageCount, pageInput, safeCurrentPage]);

    const registerCard = useCallback((id: string, node: HTMLDivElement | null) => {
        if (node) {
            cardRefs.current.set(id, node);
            return;
        }
        cardRefs.current.delete(id);
    }, []);

    useEffect(() => {
        const handleClick = (event: MouseEvent) => {
            if (
                selectedImage ||
                selectedImageIds.length === 0 ||
                suppressNextClickRef.current ||
                isInteractiveTarget(event.target) ||
                isGalleryCardTarget(event.target)
            ) return;
            clearSelectedImageIds();
            setContextMenu(null);
        };

        const handleMouseDown = (event: MouseEvent) => {
            if (selectedImage || event.button !== 0 || isInteractiveTarget(event.target)) return;
            event.preventDefault();
            selectionStartRef.current = {
                x: event.clientX,
                y: event.clientY,
                additive: event.ctrlKey || event.metaKey,
            };
            isSelectingRef.current = false;
            setContextMenu(null);
        };

        const handleMouseMove = (event: MouseEvent) => {
            const start = selectionStartRef.current;
            if (!start) return;
            const distance = Math.hypot(event.clientX - start.x, event.clientY - start.y);
            if (distance < 5 && !isSelectingRef.current) return;

            event.preventDefault();
            isSelectingRef.current = true;
            suppressNextClickRef.current = true;
            setSelectionBox({
                startX: start.x,
                startY: start.y,
                currentX: event.clientX,
                currentY: event.clientY,
            });
        };

        const handleMouseUp = (event: MouseEvent) => {
            const start = selectionStartRef.current;
            if (!start) return;

            if (isSelectingRef.current) {
                const box = {
                    startX: start.x,
                    startY: start.y,
                    currentX: event.clientX,
                    currentY: event.clientY,
                };
                const rect = rectFromPoints(box);
                const hitIds = Array.from(cardRefs.current.entries())
                    .filter(([, node]) => intersectsRect(rect, node.getBoundingClientRect()))
                    .map(([id]) => id);

                if (hitIds.length > 0) {
                    const nextIds = start.additive
                        ? Array.from(new Set([...selectedImageIds, ...hitIds]))
                        : hitIds;
                    setSelectedImageIds(nextIds);
                } else if (!start.additive) {
                    clearSelectedImageIds();
                }
            }

            selectionStartRef.current = null;
            isSelectingRef.current = false;
            setSelectionBox(null);
            window.setTimeout(() => {
                suppressNextClickRef.current = false;
            }, 0);
        };

        window.addEventListener('click', handleClick);
        window.addEventListener('mousedown', handleMouseDown);
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('click', handleClick);
            window.removeEventListener('mousedown', handleMouseDown);
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [clearSelectedImageIds, selectedImage, selectedImageIds, setSelectedImageIds]);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key !== 'Escape') return;
            clearSelectedImageIds();
            setContextMenu(null);
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [clearSelectedImageIds]);

    const handleSelect = useCallback((image: ImageItem, event: ReactMouseEvent) => {
        if (suppressNextClickRef.current) {
            suppressNextClickRef.current = false;
            return;
        }
        setContextMenu(null);
        if (event.ctrlKey || event.metaKey) {
            event.preventDefault();
            toggleSelectedImageId(image.id);
            return;
        }
        clearSelectedImageIds();
        setSelectedImage(image);
    }, [clearSelectedImageIds, setSelectedImage, toggleSelectedImageId]);

    const handleCardContextMenu = useCallback((image: ImageItem, event: ReactMouseEvent) => {
        event.preventDefault();
        const currentIds = selectedImageIds.includes(image.id) ? selectedImageIds : [image.id];
        if (!selectedImageIds.includes(image.id)) {
            setSelectedImageIds([image.id]);
        }
        setContextMenu({ x: event.clientX, y: event.clientY, ids: currentIds });
    }, [selectedImageIds, setSelectedImageIds]);

    const renderGalleryCard = useCallback((image: ImageItem, layout: GalleryCardProps['layout'] = 'css-masonry') => (
        <GalleryCard
            image={image}
            layout={layout}
            isSelected={selectedImageIdSet.has(image.id)}
            selectionColor={gallerySelectionColor}
            tagColor={galleryTagColor}
            providerName={providerLabel(image.apiType, providers)}
            providerBadge={providerBadgeClass(image.apiType)}
            providerBadgeStyle={providerBadgeStyle(image.apiType, providers)}
            onSelect={handleSelect}
            onContextMenu={handleCardContextMenu}
            registerCard={registerCard}
        />
    ), [
        gallerySelectionColor,
        galleryTagColor,
        handleCardContextMenu,
        handleSelect,
        providers,
        registerCard,
        selectedImageIdSet,
    ]);

    const renderVirtualGalleryCard = useCallback(({ data: image }: RenderComponentProps<ImageItem>) => (
        renderGalleryCard(image, 'virtual-masonry')
    ), [renderGalleryCard]);

    const contextIds = contextMenu?.ids ?? [];
    const allContextFavorites = contextIds.length > 0 && contextIds.every((id) => {
        const image = images.find((item) => item.id === id);
        return image?.isFavorite;
    });

    const runBatchAction = useCallback(async (action: () => Promise<void>) => {
        if (isBatchBusy) return;
        setIsBatchBusy(true);
        try {
            await action();
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            window.alert(message || '操作失败');
        } finally {
            setIsBatchBusy(false);
            setContextMenu(null);
        }
    }, [isBatchBusy]);

    const handleBatchDelete = useCallback(() => {
        const ids = contextMenu?.ids ?? [];
        if (!ids.length || isBatchBusy) return;
        const deleteIds = [...ids];

        setIsBatchBusy(true);
        setContextMenu(null);
        removeImagesLocal(deleteIds);
        clearSelectedImageIds();

        void batchDeleteGalleryImages(deleteIds, deleteLocalFile)
            .catch((error) => {
                const message = error instanceof Error ? error.message : String(error);
                void reloadGalleryFromServer();
                window.alert(message || '删除失败，已重新加载图廊。');
            })
            .finally(() => {
                setIsBatchBusy(false);
            });
    }, [
        clearSelectedImageIds,
        contextMenu,
        deleteLocalFile,
        isBatchBusy,
        reloadGalleryFromServer,
        removeImagesLocal,
    ]);

    const handleReusePrompt = useCallback(() => {
        const ids = contextMenu?.ids ?? [];
        if (ids.length !== 1) return;
        const image = images.find((item) => item.id === ids[0]);
        if (!image) return;
        setCurrentPrompt(normalizePromptText(image.prompt || ''));
        setContextMenu(null);
    }, [contextMenu, images, setCurrentPrompt]);

    const handleBatchExport = useCallback(() => {
        const ids = contextMenu?.ids ?? [];
        if (!ids.length) return;
        void runBatchAction(async () => {
            const result = await batchExportGalleryImages(ids);
            if (result.cancelled) return;
            if (result.skipped > 0) {
                throw new Error(
                    result.exported > 0
                        ? `另存为完成，但跳过 ${result.skipped} 张。`
                        : '另存为失败：没有可保存的图片。'
                );
            }
        });
    }, [contextMenu, runBatchAction]);

    const handleBatchAddTag = useCallback(() => {
        const ids = contextMenu?.ids ?? [];
        if (!ids.length) return;
        const tags = parseTagInput(window.prompt('输入要添加的标签，多个标签用逗号分隔'));
        if (!tags.length) return;
        void runBatchAction(async () => {
            await batchUpdateGalleryTags(ids, { add: tags });
            addTagsToImagesLocal(ids, tags);
        });
    }, [addTagsToImagesLocal, contextMenu, runBatchAction]);

    const handleBatchRemoveTag = useCallback(() => {
        const ids = contextMenu?.ids ?? [];
        if (!ids.length) return;
        const tags = parseTagInput(window.prompt('输入要移除的标签，多个标签用逗号分隔'));
        if (!tags.length) return;
        void runBatchAction(async () => {
            await batchUpdateGalleryTags(ids, { remove: tags });
            removeTagsFromImagesLocal(ids, tags);
        });
    }, [contextMenu, removeTagsFromImagesLocal, runBatchAction]);

    const handleBatchFavorite = useCallback(() => {
        const ids = contextMenu?.ids ?? [];
        if (!ids.length) return;
        const nextFavorite = !allContextFavorites;
        void runBatchAction(async () => {
            await batchFavoriteGalleryImages(ids, nextFavorite);
            setImagesFavoriteLocal(ids, nextFavorite);
        });
    }, [allContextFavorites, contextMenu, runBatchAction, setImagesFavoriteLocal]);

    if (filteredImages.length === 0) {
        if (!galleryLoaded) {
            return (
                <div className="flex-1 flex items-center justify-center">
                    <div className="text-center">
                        <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-[var(--border-subtle)] border-t-[var(--accent-primary)]" />
                        <h3 className="text-xl font-semibold text-[var(--text-primary)] mb-2">正在加载图廊</h3>
                        <p className="text-[var(--text-secondary)]">图片较多时需要一点时间</p>
                    </div>
                </div>
            );
        }
        return (
            <div className="flex-1 flex items-center justify-center">
                <div className="text-center">
                    <div className="text-6xl mb-4">🖼️</div>
                    <h3 className="text-xl font-semibold text-[var(--text-primary)] mb-2">
                        {images.length === 0 ? '还没有图片' : '没有匹配的图片'}
                    </h3>
                    <p className="text-[var(--text-secondary)]">
                        {images.length === 0
                            ? '开始生成你的第一张图片吧'
                            : '尝试调整筛选条件'}
                    </p>
                </div>
            </div>
        );
    }

    return (
        <>
            <div
                className="relative flex-1 select-none px-4 pt-6 pb-36 md:pb-40 overflow-x-hidden"
            >
                {galleryDisplayMode === 'waterfall' ? (
                    <VirtualGalleryMasonry
                        images={filteredImages}
                        columns={galleryColumns}
                        layoutSignature={virtualMasonrySignature}
                        renderCard={renderVirtualGalleryCard}
                    />
                ) : (
                    <CssMasonry
                        breakpointCols={masonryBreakpoints}
                        className="flex -ml-3"
                        columnClassName="pl-3"
                    >
                        {paginatedImages.map((image) => (
                            <div key={image.id}>
                                {renderGalleryCard(image)}
                            </div>
                        ))}
                    </CssMasonry>
                )}

                {galleryDisplayMode === 'pagination' && filteredImages.length > pageSize && (
                    <div className="flex flex-wrap items-center justify-center gap-2 py-8 text-sm">
                        <button
                            type="button"
                            disabled={safeCurrentPage <= 1}
                            onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                            className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            上一页
                        </button>
                        <div className="flex items-center gap-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2 py-1 text-[var(--text-muted)]">
                            <input
                                value={pageInput}
                                onChange={(event) => setPageInput(event.target.value.replace(/\D/g, ''))}
                                onBlur={commitPageInput}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter') {
                                        event.preventDefault();
                                        commitPageInput();
                                        event.currentTarget.blur();
                                    }
                                }}
                                className="h-6 w-12 rounded-md border border-transparent bg-transparent text-center text-sm text-[var(--text-primary)] outline-none focus:border-[var(--border-subtle)] focus:bg-[var(--bg-card)]"
                                inputMode="numeric"
                                aria-label="跳转页码"
                            />
                            <span>/ {pageCount}</span>
                        </div>
                        <button
                            type="button"
                            disabled={safeCurrentPage >= pageCount}
                            onClick={() => setCurrentPage((page) => Math.min(pageCount, page + 1))}
                            className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            下一页
                        </button>
                    </div>
                )}

            </div>

            {selectionBox && (
                <div
                    className="pointer-events-none fixed z-[70] border"
                    style={selectionBoxStyle(selectionBox, gallerySelectionBoxColor)}
                />
            )}

            {selectedImageIds.length > 0 && !contextMenu && (
                <div
                    data-no-selection="true"
                    className="fixed right-5 top-5 z-[65] flex items-center gap-3 rounded-full border border-[var(--border-subtle)] bg-[rgba(24,24,27,0.92)] px-4 py-2 text-sm text-[var(--text-secondary)] shadow-lg backdrop-blur-xl md:right-7 md:top-6"
                >
                    <span>已选 {selectedImageIds.length} 张</span>
                    <button
                        className="rounded-full border border-[var(--border-subtle)] px-2 py-0.5 text-xs text-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)]"
                        onClick={clearSelectedImageIds}
                    >
                        Esc
                    </button>
                </div>
            )}

            {contextMenu && (
                <GalleryContextMenu
                    state={contextMenu}
                    count={contextMenu.ids.length}
                    allFavorite={allContextFavorites}
                    onClose={() => setContextMenu(null)}
                    onReusePrompt={contextMenu.ids.length === 1 ? handleReusePrompt : undefined}
                    onDelete={handleBatchDelete}
                    onExport={handleBatchExport}
                    onAddTag={handleBatchAddTag}
                    onRemoveTag={handleBatchRemoveTag}
                    onFavorite={handleBatchFavorite}
                />
            )}

            {/* Image Modal */}
            {selectedImage && (
                <ImageModal
                    image={selectedImage}
                    images={filteredImages}
                    onNavigate={setSelectedImage}
                    onClose={() => setSelectedImage(null)}
                />
            )}
        </>
    );
}
