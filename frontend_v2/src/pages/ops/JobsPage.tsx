import { Copy, Eye, Search, Trash2, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { checkGenerationJob, deleteGenerationJob, deleteGenerationJobs, listGenerationJobs, type GenerationJob } from '../../services/api';
import { useProviders } from '../../hooks/useProviders';
import { providerLabel } from '../../utils/providers';
import { ReferenceImageStrip } from '../../components/shared/ReferenceImageStrip';

const statusLabels: Record<string, string> = {
    queued: '排队',
    submitting: '提交中',
    running: '运行中',
    saving: '保存中',
    succeeded: '成功',
    failed: '失败',
    cancelled: '取消',
    timeout: '超时',
};

const statusClass: Record<string, string> = {
    queued: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/30',
    submitting: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
    running: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
    saving: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
    succeeded: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    failed: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
    cancelled: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    timeout: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
};

const statusDotClass: Record<string, string> = {
    queued: 'bg-zinc-400',
    submitting: 'bg-sky-400',
    running: 'bg-blue-400',
    saving: 'bg-teal-400',
    succeeded: 'bg-emerald-400',
    failed: 'bg-rose-400',
    cancelled: 'bg-amber-400',
    timeout: 'bg-orange-400',
};

function StatusText({ status }: { status: string }) {
    return (
        <span className="inline-flex items-center gap-2 whitespace-nowrap text-xs font-medium">
            <span className={`h-2 w-2 rounded-full ${statusDotClass[status] || statusDotClass.queued}`} />
            <span className={statusClass[status]?.split(' ').find((item) => item.startsWith('text-')) || 'text-zinc-300'}>
                {statusLabels[status] || status}
            </span>
        </span>
    );
}

function fmtTime(value?: string) {
    if (!value) return '-';
    return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function duration(job: GenerationJob) {
    if (!job.started_at) return '-';
    const start = new Date(job.started_at).getTime();
    const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
    const seconds = Math.max(0, Math.floor((end - start) / 1000));
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

function requestedCount(job: GenerationJob) {
    return Number(job.params?.n || job.params?.number || job.params?.imageCount || 1);
}

function resultCount(job: GenerationJob) {
    return Array.isArray(job.result) ? job.result.length : 0;
}

function referenceCount(job: GenerationJob) {
    if (Array.isArray(job.input_images)) {
        return job.input_images.length;
    }
    const params = job.params || {};
    const inline = params.image_urls || params.input_images;
    return Array.isArray(inline) ? inline.length : 0;
}

function mergeJobListUpdates(nextJobs: GenerationJob[], currentJobs: GenerationJob[]) {
    const currentById = new Map(currentJobs.map((job) => [job.id, job]));
    return nextJobs.map((nextJob) => {
        const currentJob = currentById.get(nextJob.id);
        if (!currentJob) {
            return nextJob;
        }
        return {
            ...currentJob,
            status: nextJob.status,
            progress: nextJob.progress,
            result: nextJob.result,
            error: nextJob.error,
            external_task_id: nextJob.external_task_id,
            attempts: nextJob.attempts,
            max_attempts: nextJob.max_attempts,
            updated_at: nextJob.updated_at,
            started_at: nextJob.started_at,
            finished_at: nextJob.finished_at,
        };
    });
}

function resultImageUrl(image: Record<string, unknown> | undefined) {
    if (!image) return null;
    const localPath = image.saved_path as string | undefined;
    return localPath ? `/api/serve-image?path=${encodeURIComponent(localPath)}` : (image.url as string | undefined) || null;
}

function isActiveJob(job: GenerationJob) {
    return ['queued', 'submitting', 'running', 'saving'].includes(job.status);
}

function sortJobsByCreatedAt(jobs: GenerationJob[]) {
    return [...jobs].sort((left, right) => {
        const byCreatedAt = new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
        return byCreatedAt || right.id.localeCompare(left.id);
    });
}

export function JobsPage() {
    const [jobs, setJobs] = useState<GenerationJob[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [status, setStatus] = useState('all');
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [detailTop, setDetailTop] = useState(0);
    const [selectedResultIndex, setSelectedResultIndex] = useState(0);
    const [errorDialogJob, setErrorDialogJob] = useState<GenerationJob | null>(null);
    const pageRef = useRef<HTMLElement>(null);
    const listPanelRef = useRef<HTMLDivElement>(null);
    const detailRef = useRef<HTMLDivElement>(null);
    const { providers } = useProviders();

    const hasActiveJobs = useMemo(
        () => jobs.some(isActiveJob),
        [jobs]
    );

    const load = async (options?: { showLoading?: boolean }) => {
        if (options?.showLoading) {
            setLoading(true);
        }
        try {
            const response = await listGenerationJobs({ limit: 200 });
            if (response.success) {
                const nextJobs = sortJobsByCreatedAt(response.data || []);
                setJobs((currentJobs) => mergeJobListUpdates(nextJobs, currentJobs));
                setSelectedId((currentId) => {
                    if (currentId && nextJobs.some((job) => job.id === currentId)) {
                        return currentId;
                    }
                    return nextJobs[0]?.id || null;
                });
            }
        } finally {
            if (options?.showLoading) {
                setLoading(false);
            }
        }
    };

    useEffect(() => {
        load({ showLoading: true });
    }, []);

    useEffect(() => {
        const interval = hasActiveJobs ? 4000 : 15000;
        const timer = window.setInterval(() => load(), interval);
        return () => window.clearInterval(timer);
    }, [hasActiveJobs]);

    const filtered = useMemo(() => {
        return jobs.filter((job) => {
            if (status !== 'all' && job.status !== status) return false;
            const text = `${job.id} ${job.provider} ${providerLabel(job.provider, providers)} ${job.prompt} ${job.external_task_id || ''}`.toLowerCase();
            return !query || text.includes(query.toLowerCase());
        });
    }, [jobs, providers, query, status]);

    const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
    const currentPage = Math.min(page, totalPages);
    const pagedJobs = useMemo(() => {
        const start = (currentPage - 1) * pageSize;
        return filtered.slice(start, start + pageSize);
    }, [currentPage, filtered, pageSize]);

    useEffect(() => {
        setPage(1);
    }, [query, status, pageSize]);

    useEffect(() => {
        if (page > totalPages) {
            setPage(totalPages);
        }
    }, [page, totalPages]);

    useEffect(() => {
        if (!pagedJobs.length) {
            setSelectedId(null);
            return;
        }
        if (!selectedId || !pagedJobs.some((job) => job.id === selectedId)) {
            setSelectedId(pagedJobs[0].id);
        }
    }, [pagedJobs, selectedId]);

    useEffect(() => {
        if (!selectedId) return;
        let cancelled = false;
        checkGenerationJob(selectedId).then((response) => {
            if (cancelled || !response.success || !response.data) return;
            setJobs((items) => items.map((job) => (job.id === selectedId ? { ...job, ...response.data } : job)));
        }).catch(() => undefined);
        return () => {
            cancelled = true;
        };
    }, [selectedId]);

    const selected = pagedJobs.find((job) => job.id === selectedId) || pagedJobs[0];
    const selectedResults = Array.isArray(selected?.result) ? selected.result : [];
    const pageStart = filtered.length ? (currentPage - 1) * pageSize + 1 : 0;
    const pageEnd = Math.min(currentPage * pageSize, filtered.length);
    const counts = {
        queued: jobs.filter((j) => j.status === 'queued').length,
        running: jobs.filter((j) => ['submitting', 'running', 'saving'].includes(j.status)).length,
        succeeded: jobs.filter((j) => j.status === 'succeeded').length,
        failed: jobs.filter((j) => ['failed', 'timeout', 'cancelled'].includes(j.status)).length,
    };

    const handleDeleteJob = async (job: GenerationJob) => {
        const message = isActiveJob(job)
            ? '这只会删除运行中心记录，不会取消正在执行的外部生成。确定删除吗？'
            : '确定删除这条运行记录吗？';
        if (!window.confirm(message)) return;
        const response = await deleteGenerationJob(job.id);
        if (!response.success) {
            window.alert(response.error?.message || '删除失败');
            return;
        }
        setJobs((items) => items.filter((item) => item.id !== job.id));
        setSelectedId((current) => (current === job.id ? null : current));
        void load();
    };

    const handleClearJobs = async () => {
        if (!jobs.length) return;
        const activeCount = jobs.filter(isActiveJob).length;
        const message = activeCount
            ? `确定清空全部 ${jobs.length} 条运行中心记录吗？其中 ${activeCount} 条仍在运行；这不会取消外部生成，只会删除运行中心记录。`
            : `确定清空全部 ${jobs.length} 条运行中心记录吗？`;
        if (!window.confirm(message)) return;

        const response = await deleteGenerationJobs({ includeActive: true });
        if (!response.success) {
            window.alert(response.error?.message || '清空失败');
            return;
        }
        setJobs([]);
        setSelectedId(null);
        setSelectedResultIndex(0);
        setPage(1);
    };

    const clampDetailTop = (top: number) => {
        const pageTop = pageRef.current?.getBoundingClientRect().top || 0;
        const viewportHeight = window.innerHeight;
        const listHeight = listPanelRef.current?.offsetHeight || 0;
        const detailHeight = detailRef.current?.offsetHeight || 0;
        const viewportMaxTop = viewportHeight - pageTop - detailHeight - 24;
        const listMaxTop = listHeight - detailHeight;
        const maxTop = Math.max(0, Math.min(viewportMaxTop, listMaxTop));
        return Math.max(0, Math.min(top, maxTop));
    };

    const selectJobAtRow = (jobId: string, rowElement: HTMLTableRowElement) => {
        if (jobId === selectedId) {
            return;
        }
        setSelectedId(jobId);
        setSelectedResultIndex(0);
        const pageRect = pageRef.current?.getBoundingClientRect();
        const rowRect = rowElement.getBoundingClientRect();
        const pageTop = pageRect?.top || 0;
        const rawTop = rowRect.top - pageTop;
        setDetailTop(clampDetailTop(rawTop));
    };

    useEffect(() => {
        if (selectedResultIndex >= selectedResults.length) {
            setSelectedResultIndex(0);
        }
    }, [selectedResultIndex, selectedResults.length]);

    useLayoutEffect(() => {
        setDetailTop((top) => clampDetailTop(top));
    }, [selectedId, selectedResultIndex, selectedResults.length, pageSize, currentPage, filtered.length]);

    return (
        <section ref={pageRef} className="grid min-h-[calc(100vh-9rem)] grid-cols-[minmax(0,1fr)_380px] gap-4">
            <div ref={listPanelRef} className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)]">
                <div className="flex flex-wrap items-center gap-3 border-b border-[var(--border-subtle)] p-3">
                    <div className="relative min-w-72 flex-1">
                        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-muted)]" />
                        <input
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="搜索任务、渠道或提示词..."
                            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] py-2 pl-9 pr-3 text-sm text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--accent-primary)]"
                        />
                    </div>
                    {[
                        ['all', `全部 ${jobs.length}`],
                        ['queued', `排队 ${counts.queued}`],
                        ['running', `运行 ${counts.running}`],
                        ['succeeded', `成功 ${counts.succeeded}`],
                        ['failed', `失败 ${counts.failed}`],
                    ].map(([value, label]) => (
                        <button
                            key={value}
                            onClick={() => setStatus(value)}
                            className={`rounded-full px-3 py-1.5 text-sm transition-colors ${status === value ? 'bg-[var(--accent-primary)] text-white' : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
                        >
                            {label}
                        </button>
                    ))}
                    <button
                        onClick={handleClearJobs}
                        disabled={!jobs.length}
                        className="ml-auto inline-flex items-center gap-2 rounded-full border border-rose-500/35 bg-rose-500/10 px-3 py-1.5 text-sm text-rose-200 transition-all hover:border-rose-400/70 hover:bg-rose-500/28 hover:text-white hover:shadow-[0_0_18px_rgba(244,63,94,0.22)] active:scale-[0.98] disabled:cursor-not-allowed disabled:border-[var(--border-subtle)] disabled:bg-[var(--bg-secondary)] disabled:text-[var(--text-muted)] disabled:shadow-none"
                        title="删除所有任务记录"
                    >
                        <Trash2 className="h-4 w-4" />
                        清空记录
                    </button>
                </div>

                <div className="overflow-auto">
                    <table className="w-full min-w-[980px] table-fixed text-left text-sm">
                        <thead className="sticky top-0 z-10 bg-[var(--bg-secondary)] text-xs text-[var(--text-muted)]">
                            <tr>
                                <th className="w-[26%] px-4 py-3 font-medium">任务</th>
                                <th className="w-[12%] px-3 py-3 font-medium">渠道</th>
                                <th className="w-[8%] px-3 py-3 font-medium">状态</th>
                                <th className="w-[14%] px-3 py-3 font-medium">进度</th>
                                <th className="w-[11%] px-3 py-3 font-medium">模型</th>
                                <th className="w-[6%] px-3 py-3 font-medium">数量</th>
                                <th className="w-[6%] px-3 py-3 font-medium">耗时</th>
                                <th className="w-[10%] px-3 py-3 font-medium">创建时间</th>
                                <th className="w-[7%] px-3 py-3 font-medium">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {pagedJobs.map((job) => (
                                <tr
                                    key={job.id}
                                    onClick={(event) => selectJobAtRow(job.id, event.currentTarget)}
                                    className={`cursor-pointer border-b border-[var(--border-subtle)] transition-colors hover:bg-[var(--bg-card-hover)] ${selected?.id === job.id ? 'bg-[var(--accent-primary)]/12 shadow-[inset_3px_0_0_var(--accent-primary)]' : ''}`}
                                >
                                    <td className="max-w-[360px] px-4 py-3">
                                        <div className="font-mono text-xs text-[var(--text-muted)]">{job.id.slice(0, 12)}</div>
                                        <div className="truncate text-[var(--text-primary)]">{job.prompt || '无 Prompt'}</div>
                                    </td>
                                    <td className="truncate px-3 py-3 text-[var(--text-secondary)]">{providerLabel(job.provider, providers)}</td>
                                    <td className="px-3 py-3">
                                        <StatusText status={job.status} />
                                    </td>
                                    <td className="px-3 py-3">
                                        <div className="flex items-center gap-2">
                                            <div className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-800">
                                                <div className="h-full rounded-full bg-[var(--accent-primary)]" style={{ width: `${Math.min(100, job.progress || 0)}%` }} />
                                            </div>
                                            <span className="w-9 text-xs text-[var(--text-muted)]">{job.progress || 0}%</span>
                                        </div>
                                    </td>
                                    <td className="truncate px-3 py-3 text-[var(--text-secondary)]">{String(job.params?.model || '-')}</td>
                                    <td className="px-3 py-3 text-[var(--text-secondary)]">
                                        <span className="font-mono text-xs">
                                            {resultCount(job)}/{requestedCount(job)}
                                        </span>
                                    </td>
                                    <td className="px-3 py-3 font-mono text-xs text-[var(--text-secondary)]">{duration(job)}</td>
                                    <td className="px-3 py-3 text-[var(--text-secondary)]">{fmtTime(job.created_at)}</td>
                                    <td className="px-3 py-3">
                                        <div className="flex items-center gap-1 text-[var(--text-muted)]">
                                            <button
                                                className="rounded p-1 disabled:cursor-not-allowed disabled:opacity-30 enabled:hover:bg-[var(--bg-secondary)] enabled:hover:text-[var(--text-primary)]"
                                                title="查看图像"
                                                aria-label="查看图像"
                                                disabled={!resultImageUrl((job.result || [])[selected?.id === job.id ? selectedResultIndex : 0])}
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    const index = selected?.id === job.id ? selectedResultIndex : 0;
                                                    const url = resultImageUrl((job.result || [])[index]);
                                                    setSelectedId(job.id);
                                                    if (selected?.id !== job.id) {
                                                        setSelectedResultIndex(0);
                                                    }
                                                    if (url) window.open(url, '_blank', 'noopener,noreferrer');
                                                }}
                                            >
                                                <Eye className="h-4 w-4" />
                                            </button>
                                            <button
                                                className="rounded p-1 hover:bg-rose-500/15 hover:text-rose-300"
                                                title="删除任务记录"
                                                aria-label="删除任务记录"
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    void handleDeleteJob(job);
                                                }}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {!filtered.length && (
                        <div className="p-12 text-center text-sm text-[var(--text-muted)]">
                            {loading ? '正在加载任务...' : '暂无任务记录'}
                        </div>
                    )}
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border-subtle)] px-4 py-3 text-sm text-[var(--text-secondary)]">
                    <div>
                        显示第 {pageStart} 到 {pageEnd} 条，共 {filtered.length} 条
                    </div>
                    <div className="flex items-center gap-3">
                        <label className="flex items-center gap-2">
                            每页
                            <select
                                value={pageSize}
                                onChange={(event) => setPageSize(Number(event.target.value))}
                                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-2 py-1 text-[var(--text-primary)] outline-none"
                            >
                                {[25, 50, 100, 200].map((size) => (
                                    <option key={size} value={size}>{size}</option>
                                ))}
                            </select>
                        </label>
                        <div className="flex items-center overflow-hidden rounded-lg border border-[var(--border-subtle)]">
                            <button
                                className="px-3 py-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                                disabled={currentPage <= 1}
                                onClick={() => setPage((value) => Math.max(1, value - 1))}
                            >
                                上一页
                            </button>
                            <span className="border-x border-[var(--border-subtle)] px-3 py-1.5 text-[var(--text-primary)]">
                                {currentPage} / {totalPages}
                            </span>
                            <button
                                className="px-3 py-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                                disabled={currentPage >= totalPages}
                                onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                            >
                                下一页
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <aside className="min-w-0">
                {selected ? (
                    <div
                        ref={detailRef}
                        className="flex flex-col rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] transition-transform duration-200 ease-out"
                        style={{ transform: `translateY(${detailTop}px)` }}
                    >
                        <div className="border-b border-[var(--border-subtle)] p-4">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-lg font-semibold text-[var(--text-primary)]">任务详情</div>
                                    <div className="font-mono text-xs text-[var(--text-muted)]">{selected.id}</div>
                                </div>
                                <span className={`rounded-full border px-2 py-1 text-xs ${statusClass[selected.status] || statusClass.queued}`}>
                                    {statusLabels[selected.status] || selected.status}
                                </span>
                            </div>
                        </div>
                        <div className="space-y-4 p-4">
                            <section>
                                <div className="mb-2 text-xs font-medium text-[var(--text-muted)]">Prompt</div>
                                <div className="max-h-44 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-3 text-sm text-[var(--text-secondary)]">
                                    {selected.prompt || '-'}
                                </div>
                            </section>
                            <section className="grid grid-cols-2 gap-2 text-sm">
                                {[
                                    ['渠道', providerLabel(selected.provider, providers)],
                                    ['模型', String(selected.params?.model || '-')],
                                    ['比例', String(selected.params?.size || selected.params?.ratio || '-')],
                                    ['数量', `${resultCount(selected)}/${requestedCount(selected)}`],
                                    ['Provider任务ID', selected.external_task_id || '-'],
                                ].map(([label, value]) => (
                                    <div key={label} className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-3">
                                        <div className="text-xs text-[var(--text-muted)]">{label}</div>
                                        <div className="mt-1 truncate text-[var(--text-primary)]">{value}</div>
                                    </div>
                                ))}
                                <button
                                    type="button"
                                    disabled={!selected.error}
                                    onClick={() => setErrorDialogJob(selected)}
                                    className="col-span-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-3 text-left transition-colors enabled:hover:border-rose-400/45 enabled:hover:bg-rose-500/8 disabled:cursor-default"
                                >
                                    <div className="flex items-center justify-between gap-3">
                                        <div className="text-xs text-[var(--text-muted)]">失败原因</div>
                                        {selected.error && <span className="text-xs text-rose-300">查看完整</span>}
                                    </div>
                                    <div className={`mt-1 line-clamp-2 break-words text-[var(--text-primary)] ${selected.error ? 'text-rose-100/90' : ''}`}>
                                        {selected.error || '-'}
                                    </div>
                                </button>
                            </section>
                            <section>
                                <div className="mb-2 flex items-center justify-between gap-3">
                                    <div className="text-xs font-medium text-[var(--text-muted)]">参考图</div>
                                    <div className="text-xs text-[var(--text-muted)]">{referenceCount(selected)} 张</div>
                                </div>
                                <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-3">
                                    <ReferenceImageStrip images={selected.input_images} max={12} />
                                </div>
                            </section>
                            <section>
                                <div className="mb-2 text-xs font-medium text-[var(--text-muted)]">结果预览</div>
                                <div className="grid grid-cols-2 gap-2">
                                    {(selected.result || []).map((img, index) => {
                                        const url = resultImageUrl(img);
                                        const isSelected = index === selectedResultIndex;
                                        return (
                                            <button
                                                key={`${url}-${index}`}
                                                className={`overflow-hidden rounded-lg border bg-[var(--bg-secondary)] text-left transition-colors ${isSelected ? 'border-[var(--accent-primary)] ring-2 ring-[var(--accent-primary)]/30' : 'border-[var(--border-subtle)] hover:border-[var(--text-muted)]'}`}
                                                title={url ? `选择结果 ${index + 1}` : '结果不可打开'}
                                                onClick={() => setSelectedResultIndex(index)}
                                            >
                                                <div className="aspect-square w-full overflow-hidden bg-zinc-900">
                                                    {url ? <img src={url} className="h-full w-full object-cover transition-transform hover:scale-[1.03]" /> : null}
                                                </div>
                                            </button>
                                        );
                                    })}
                                    {!selected.result?.length && <div className="col-span-2 rounded-lg border border-dashed border-[var(--border-subtle)] p-6 text-center text-sm text-[var(--text-muted)]">暂无结果</div>}
                                </div>
                            </section>
                        </div>
                    </div>
                ) : (
                    <div
                        ref={detailRef}
                        className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-8 text-center text-sm text-[var(--text-muted)]"
                    >
                        选择一个任务查看详情
                    </div>
                )}
            </aside>
            {errorDialogJob?.error && (
                <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm" onClick={() => setErrorDialogJob(null)}>
                    <div
                        className="w-full max-w-3xl overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] shadow-2xl"
                        onClick={(event) => event.stopPropagation()}
                    >
                        <div className="flex items-start justify-between gap-4 border-b border-[var(--border-subtle)] p-4">
                            <div>
                                <div className="text-lg font-semibold text-[var(--text-primary)]">失败原因</div>
                                <div className="mt-1 font-mono text-xs text-[var(--text-muted)]">{errorDialogJob.id}</div>
                            </div>
                            <button
                                type="button"
                                onClick={() => setErrorDialogJob(null)}
                                className="rounded-lg p-2 text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)]"
                                aria-label="关闭"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                        <div className="max-h-[65vh] overflow-auto p-4">
                            <pre className="whitespace-pre-wrap break-words rounded-lg border border-rose-500/20 bg-rose-500/8 p-4 font-mono text-xs leading-relaxed text-rose-100/90">
                                {errorDialogJob.error}
                            </pre>
                        </div>
                        <div className="flex justify-end gap-2 border-t border-[var(--border-subtle)] p-4">
                            <button
                                type="button"
                                onClick={() => void navigator.clipboard?.writeText(errorDialogJob.error || '')}
                                className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)]"
                            >
                                <Copy className="h-4 w-4" />
                                复制
                            </button>
                            <button
                                type="button"
                                onClick={() => setErrorDialogJob(null)}
                                className="rounded-lg bg-[var(--accent-primary)] px-3 py-2 text-sm text-white transition-opacity hover:opacity-90"
                            >
                                关闭
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </section>
    );
}
