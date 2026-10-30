import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, AudioLines, Check, ChevronDown, ChevronRight, CircleHelp, Clock3, Cpu,
  Download, FileAudio, FileMusic, FileText, Headphones, LoaderCircle, Music2, Pause, Play, Plus,
  FolderOpen, Home, Mic2, Pencil, Radio, RefreshCw, Sparkles, Square, Trash2, Upload, Volume2, VolumeX, WandSparkles, X,
} from 'lucide-react';
import { api, uploadProject, watchJob } from './api';
import type { Health, Job, Project, SingerProfile, WorkspaceSummary } from './types';
import MidiStudio from './MidiStudio';

const LOCAL_PROJECT_KEY = 'yingstudio:last-project';
const LOCAL_SINGER_KEY = 'yingstudio:last-singer';
const TERMINAL = ['succeeded', 'failed', 'canceled'];
const lyricsDraftKey = (id: string) => `yingstudio:lyrics:${id}`;

function formatClock(seconds: number) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

function formatLrcTimestamp(seconds: number) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toFixed(2).padStart(5, '0');
  return `${mins}:${secs}`;
}

function parseLrcTimestamp(value: string): number | null {
  const match = value.trim().match(/^(\d{1,3}):([0-5]?\d)(?:[.:](\d{1,3}))?$/);
  if (!match) return null;
  const minutes = Number(match[1]);
  const seconds = Number(match[2]);
  const fraction = match[3] ? Number(`0.${match[3]}`) : 0;
  return Math.round((minutes * 60 + seconds + fraction) * 100) / 100;
}

const timingDraftKey = (projectId: string, index: number, edge: 'start' | 'end') => `${projectId}:${index}:${edge}`;

function statusLabel(status?: string) {
  if (status === 'running') return '正在执行';
  if (status === 'queued') return '排队中';
  if (status === 'succeeded') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'canceled') return '已取消';
  return '等待中';
}

function projectAsset(id: string, asset: string) {
  return `/api/projects/${id}/assets/${asset}`;
}

function workspaceDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function workspaceState(status: string) {
  if (status === 'ready') return '可继续';
  if (status === 'preparing') return '准备中';
  if (status === 'failed') return '需要重试';
  return status;
}

type WorkspaceHomeProps = {
  workspaces: WorkspaceSummary[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
  onNewRewrite: () => void;
  onNewMidi: () => void;
};

function WorkspaceHome({ workspaces, loading, error, onRefresh, onNewRewrite, onNewMidi }: WorkspaceHomeProps) {
  return (
    <div className="workspace-home">
      <section className="workspace-home-hero">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" />SONG OF THESEUS <span className="eyebrow-mono">/ 工作区中心</span></div>
          <h1>从上次停下的地方，<em>继续创作。</em></h1>
          <p>历史工程和歌词草稿保存在这台电脑上；打开任意工作区即可接着编辑。</p>
        </div>
        <div className="workspace-home-actions">
          <button className="button button-primary" onClick={onNewRewrite}><Plus size={15} />新建逐句改唱</button>
          <button className="button button-quiet" onClick={onNewMidi}><FileMusic size={15} />新建 MIDI 填词</button>
        </div>
      </section>

      <section className="workspace-library panel">
        <div className="workspace-library-head">
          <div><div className="step-chip"><FolderOpen size={13} /> WORKSPACE LIBRARY</div><h2>历史工作区</h2><p>选择一个项目，恢复歌词、时间点和未完成的任务。</p></div>
          <button className="button button-quiet" onClick={onRefresh}><RefreshCw size={13} />刷新</button>
        </div>
        {error && <div className="workspace-error">{error}</div>}
        {loading ? (
          <div className="workspace-empty"><LoaderCircle size={18} className="spin" />正在读取本机工作区…</div>
        ) : workspaces.length === 0 ? (
          <div className="workspace-empty"><FolderOpen size={20} /><span>这里还没有历史工程。新建一个逐句改唱或 MIDI 工作区开始创作。</span></div>
        ) : (
          <div className="workspace-list">
            {workspaces.map((item) => (
              <a className="workspace-card" key={item.id} href={`/?project=${encodeURIComponent(item.id)}&type=${item.workflow_type}`}>
                <span className={`workspace-card-icon ${item.workflow_type}`}>
                  {item.workflow_type === 'midi' ? <FileMusic size={18} /> : <Music2 size={18} />}
                </span>
                <span className="workspace-card-main">
                  <span className="workspace-card-title"><b>{item.title}</b><i className={item.status === 'failed' ? 'failed' : item.status === 'preparing' ? 'preparing' : ''}>{workspaceState(item.status)}</i></span>
                  <span className="workspace-card-meta">
                    <span>{item.workflow_type === 'midi' ? 'MIDI 填词' : '逐句改唱'}</span><span>·</span>
                    <span>{formatClock(item.duration)}</span><span>·</span>
                    <span>{item.line_count} 句</span>
                    {item.singer_name && <><span>·</span><span>歌手 {item.singer_name}</span></>}
                  </span>
                </span>
                <span className="workspace-card-date"><Clock3 size={12} />{workspaceDate(item.updated_at || item.created_at)}</span>
                <span className="workspace-card-open">继续<ChevronRight size={15} /></span>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function App() {
  const initialParams = new URLSearchParams(window.location.search);
  const initialProjectId = initialParams.get('project')?.trim() || '';
  const initialWorkspace = initialParams.get('type') === 'midi' ? 'midi' : 'rewrite';
  const [workspace, setWorkspace] = useState<'rewrite' | 'midi'>(initialWorkspace);
  const [showHome, setShowHome] = useState(!initialProjectId);
  const [initialMidiProjectId, setInitialMidiProjectId] = useState<string | null>(initialWorkspace === 'midi' && initialProjectId ? initialProjectId : null);
  const [midiProjectRefreshToken, setMidiProjectRefreshToken] = useState(0);
  const [health, setHealth] = useState<Health | null>(null);
  const [queue, setQueue] = useState<Job[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [workspacesLoading, setWorkspacesLoading] = useState(true);
  const [workspacesError, setWorkspacesError] = useState('');
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [steps, setSteps] = useState(32);
  const [pitchShift, setPitchShift] = useState(0);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [lrcFile, setLrcFile] = useState<File | null>(null);
  const [lrcText, setLrcText] = useState('');
  const [autoTranscribe, setAutoTranscribe] = useState(true);
  const [singers, setSingers] = useState<SingerProfile[]>([]);
  const [selectedSingerId, setSelectedSingerId] = useState(() => localStorage.getItem(LOCAL_SINGER_KEY) || '');
  const [showSingerManager, setShowSingerManager] = useState(false);
  const [editingSingerId, setEditingSingerId] = useState<string | null>(null);
  const [singerName, setSingerName] = useState('');
  const [singerReferenceText, setSingerReferenceText] = useState('');
  const [singerAudioFile, setSingerAudioFile] = useState<File | null>(null);
  const [singerSaving, setSingerSaving] = useState(false);
  const [singerError, setSingerError] = useState('');
  const [projectSingerSaving, setProjectSingerSaving] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [comparison, setComparison] = useState<{ index: number; mode: 'original' | 'generated'; src: string; autoPlay?: boolean } | null>(null);
  const [previewJobs, setPreviewJobs] = useState<Record<number, Job>>({});
  const [timingDrafts, setTimingDrafts] = useState<Record<string, string>>({});
  const [timingSaving, setTimingSaving] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const [showLrcPaste, setShowLrcPaste] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const lrcInput = useRef<HTMLInputElement>(null);
  const singerAudioInput = useRef<HTMLInputElement>(null);
  const eventSources = useRef(new Map<string, EventSource>());

  const refreshProject = useCallback(async (id: string) => {
    const next = await api.project(id);
    // Project metadata is persisted by the API, while unsaved lyric edits live
    // in localStorage. Merge the browser draft on every refresh so a completed
    // preview/job cannot silently reset the text the user is editing.
    try {
      const draft = JSON.parse(localStorage.getItem(lyricsDraftKey(id)) || 'null') as string[] | null;
      if (Array.isArray(draft)) {
        next.segments = next.segments.map((line, index) => ({
          ...line,
          edited: typeof draft[index] === 'string' ? draft[index] : line.edited,
        }));
      }
    } catch {
      localStorage.removeItem(lyricsDraftKey(id));
    }
    setProject(next);
    return next;
  }, []);

  const refreshWorkspaces = useCallback(async () => {
    setWorkspacesError('');
    try {
      setWorkspaces(await api.workspaces());
    } catch (cause) {
      setWorkspacesError(cause instanceof Error ? cause.message : '读取历史工作区失败。');
    } finally {
      setWorkspacesLoading(false);
    }
  }, []);

  const connectJob = useCallback((jobId: string, projectId: string, previewIndex?: number) => {
    eventSources.current.get(jobId)?.close();
    const events = watchJob(jobId, (job) => {
      if (previewIndex === undefined) {
        setActiveJob(job);
      } else {
        setPreviewJobs((current) => ({ ...current, [previewIndex]: job }));
      }
      if (!TERMINAL.includes(job.status)) return;
      eventSources.current.get(jobId)?.close();
      eventSources.current.delete(jobId);
      void api.queue().then(setQueue).catch(() => undefined);
      if (job.status === 'failed') setError(job.error || '任务失败，请查看状态详情。');
      if (job.status === 'succeeded') {
        setError('');
        setToast(job.label || '任务已完成');
        window.setTimeout(() => setToast(''), 4200);
        if (previewIndex !== undefined) {
          setComparison({
            index: previewIndex,
            mode: 'generated',
            src: `/api/projects/${projectId}/lines/${previewIndex}/preview/generated?t=${Date.now()}`,
            autoPlay: true,
          });
        }
      }
      void refreshProject(projectId).catch(() => undefined);
    });
    eventSources.current.set(jobId, events);
  }, [refreshProject]);

  useEffect(() => {
    let live = true;
    const refresh = () => {
      api.health().then((value) => live && setHealth(value)).catch(() => live && setHealth(null));
      api.queue().then((value) => live && setQueue(value)).catch(() => undefined);
    };
    refresh();
    void refreshWorkspaces();
    const workspaceTimer = window.setInterval(() => void refreshWorkspaces(), 15000);
    api.singers().then((profiles) => {
      if (!live) return;
      setSingers(profiles);
      const savedSinger = localStorage.getItem(LOCAL_SINGER_KEY);
      if (savedSinger && !profiles.some((profile) => profile.id === savedSinger)) {
        localStorage.removeItem(LOCAL_SINGER_KEY);
        setSelectedSingerId('');
      }
    }).catch(() => undefined);
    const timer = window.setInterval(refresh, 5000);
    const requestedProject = new URLSearchParams(window.location.search).get('project')?.trim();
    const requestedType = new URLSearchParams(window.location.search).get('type');
    if (requestedProject && requestedType !== 'midi') {
      setShowHome(false);
      localStorage.setItem(LOCAL_PROJECT_KEY, requestedProject);
      api.project(requestedProject).then((saved) => {
        if (!live) return;
        try {
          const draft = JSON.parse(localStorage.getItem(lyricsDraftKey(requestedProject)) || 'null') as string[] | null;
          if (Array.isArray(draft)) {
            saved.segments = saved.segments.map((line, index) => ({
              ...line,
              edited: typeof draft[index] === 'string' ? draft[index] : line.edited,
            }));
          }
        } catch {
          localStorage.removeItem(lyricsDraftKey(requestedProject));
        }
        setProject(saved);
        const unfinished = saved.jobs.find((job) => job.status === 'queued' || job.status === 'running');
        const latest = unfinished || saved.jobs[0];
        if (latest && !latest.parent_id) {
          if (latest.kind === 'preview') {
            const index = (latest as Job & { line_index?: number }).line_index;
            if (typeof index === 'number') {
              const isBusy = latest.status === 'queued' || latest.status === 'running';
              const hasMatchingPreview = saved.latest_previews?.[String(index)]?.lyrics === saved.segments[index]?.edited;
              setSelectedIndex(index);
              setPreviewJobs((current) => ({ ...current, [index]: latest }));
              setComparison({
                index,
                mode: 'generated',
                src: hasMatchingPreview ? `/api/projects/${requestedProject}/lines/${index}/preview/generated?t=${Date.now()}` : '',
                autoPlay: false,
              });
              if (isBusy) connectJob(latest.id, requestedProject, index);
              return;
            }
          }
          setSelectedIndex(0);
          setActiveJob(latest);
          if (latest.status === 'queued' || latest.status === 'running') connectJob(latest.id, requestedProject);
        } else {
          setSelectedIndex(0);
        }
      }).catch((cause) => {
        if (!live) return;
        setShowHome(true);
        setWorkspacesError(cause instanceof Error ? cause.message : '无法打开这个工作区。');
        localStorage.removeItem(LOCAL_PROJECT_KEY);
      });
    } else if (requestedProject && requestedType === 'midi') {
      setShowHome(false);
      setWorkspace('midi');
    }
    return () => {
      live = false;
      window.clearInterval(timer);
      window.clearInterval(workspaceTimer);
      eventSources.current.forEach((source) => source.close());
      eventSources.current.clear();
    };
  }, [connectJob, refreshWorkspaces]);

  useEffect(() => {
    if (!project) return;
    try {
      localStorage.setItem(
        lyricsDraftKey(project.id),
        JSON.stringify(project.segments.map((line) => line.edited)),
      );
    } catch {
      // The project remains usable when browser storage is unavailable/full.
    }
  }, [project?.id, project?.segments]);

  useEffect(() => {
    if (!project || project.status !== 'ready') return;
    const timer = window.setTimeout(() => {
      void api.saveLyricsDraft(
        project.id,
        project.segments.map((line) => line.edited),
      ).catch((cause) => setError(cause instanceof Error ? cause.message : '保存歌词草稿失败。'));
    }, 650);
    return () => window.clearTimeout(timer);
  }, [project?.id, project?.segments, project?.status]);

  const changedCount = project?.segments.filter((line) => line.original.trim() !== line.edited.trim()).length ?? 0;
  const selectedLine = project?.segments[selectedIndex];
  const generationBusy = activeJob?.kind === 'generate_song' && (activeJob.status === 'queued' || activeJob.status === 'running');
  const previewBusy = Object.values(previewJobs).some((job) => job.status === 'queued' || job.status === 'running');
  const timingLocked = timingSaving || project?.status !== 'ready' || generationBusy || previewBusy;

  const startNewProject = () => {
    setShowHome(false);
    setWorkspace('rewrite');
    window.history.replaceState(null, '', '/');
    localStorage.removeItem(LOCAL_PROJECT_KEY);
    setProject(null);
    setActiveJob(null);
    setComparison(null);
    setAudioFile(null);
    setLrcFile(null);
    setLrcText('');
    setError('');
    setToast('');
  };

  const startNewMidiProject = () => {
    setShowHome(false);
    setWorkspace('midi');
    setInitialMidiProjectId(null);
    window.history.replaceState(null, '', '/');
    localStorage.removeItem('yingstudio:last-midi-project');
    setProject(null);
    setActiveJob(null);
    setError('');
    setToast('');
  };

  const goHome = () => {
    setShowHome(true);
    setWorkspace('rewrite');
    window.history.pushState(null, '', '/');
    void refreshWorkspaces();
  };

  const openSingerManager = () => {
    setSingerError('');
    setShowSingerManager(true);
    void api.singers().then(setSingers).catch((cause) => setSingerError(cause instanceof Error ? cause.message : '无法读取歌手库。'));
  };

  const resetSingerForm = () => {
    setEditingSingerId(null);
    setSingerName('');
    setSingerReferenceText('');
    setSingerAudioFile(null);
    if (singerAudioInput.current) singerAudioInput.current.value = '';
  };

  const editSinger = (profile: SingerProfile) => {
    setEditingSingerId(profile.id);
    setSingerName(profile.name);
    setSingerReferenceText(profile.reference_text);
    setSingerAudioFile(null);
    setSingerError('');
    if (singerAudioInput.current) singerAudioInput.current.value = '';
  };

  const saveSinger = async () => {
    if (!singerName.trim() || !singerReferenceText.trim()) {
      setSingerError('请填写歌手名称和参考歌词。');
      return;
    }
    if (!editingSingerId && !singerAudioFile) {
      setSingerError('新建歌手资料时，请上传一段参考人声。');
      return;
    }
    setSingerSaving(true);
    setSingerError('');
    try {
      const saved = editingSingerId
        ? await api.updateSinger(editingSingerId, singerName, singerReferenceText, singerAudioFile)
        : await api.createSinger(singerName, singerReferenceText, singerAudioFile!);
      setSingers((current) => [...current.filter((item) => item.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')));
      resetSingerForm();
      setToast(`歌手「${saved.name}」已保存。`);
      window.setTimeout(() => setToast(''), 4200);
    } catch (cause) {
      setSingerError(cause instanceof Error ? cause.message : '歌手资料保存失败。');
    } finally {
      setSingerSaving(false);
    }
  };

  const chooseSinger = (singerId: string) => {
    setSelectedSingerId(singerId);
    if (singerId) localStorage.setItem(LOCAL_SINGER_KEY, singerId);
    else localStorage.removeItem(LOCAL_SINGER_KEY);
  };

  const removeSinger = async (profile: SingerProfile) => {
    const accepted = window.confirm(`删除「${profile.name}」歌手资料？已应用到工程的参考音频副本不会受影响。`);
    if (!accepted) return;
    try {
      await api.deleteSinger(profile.id);
      setSingers((current) => current.filter((item) => item.id !== profile.id));
      if (selectedSingerId === profile.id) chooseSinger('');
      if (editingSingerId === profile.id) resetSingerForm();
      setToast(`已删除歌手资料「${profile.name}」。`);
      window.setTimeout(() => setToast(''), 4200);
    } catch (cause) {
      setSingerError(cause instanceof Error ? cause.message : '删除失败。');
    }
  };

  const applyProjectSinger = async (singerId: string) => {
    if (!project || projectSingerSaving) return;
    setProjectSingerSaving(true);
    setError('');
    try {
      const result = await api.setProjectSinger(project.id, singerId || null);
      chooseSinger(singerId);
      setProject((current) => current ? {
        ...current,
        singer_profile: result.singer_profile ?? null,
        latest_previews: result.invalidated_previews ? {} : current.latest_previews,
        last_generation: result.final_invalidated ? null : current.last_generation,
      } : current);
      if (result.invalidated_previews) {
        setPreviewJobs({});
        setComparison(null);
      }
      setToast(singerId ? `已将「${result.singer_profile?.name || '所选歌手'}」设为本工程歌手。` : '本工程已恢复使用原曲歌手。');
      window.setTimeout(() => setToast(''), 4200);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '切换歌手失败。');
    } finally {
      setProjectSingerSaving(false);
    }
  };

  const selectSingerFromLibrary = async (profile: SingerProfile) => {
    if (workspace === 'rewrite' && project) {
      await applyProjectSinger(profile.id);
      setShowSingerManager(false);
      return;
    }
    if (workspace === 'midi') {
      const midiProjectId = localStorage.getItem('yingstudio:last-midi-project');
      if (midiProjectId) {
        setSingerError('');
        try {
          await api.setProjectSinger(midiProjectId, profile.id);
          chooseSinger(profile.id);
          setMidiProjectRefreshToken((value) => value + 1);
          setToast(`已将「${profile.name}」设为本 MIDI 工程统一歌手。`);
          window.setTimeout(() => setToast(''), 4200);
        } catch (cause) {
          setSingerError(cause instanceof Error ? cause.message : '无法将歌手应用到 MIDI 工程。');
          return;
        }
      } else {
        chooseSinger(profile.id);
      }
      setShowSingerManager(false);
      return;
    }
    chooseSinger(profile.id);
    setShowSingerManager(false);
  };

  const onUpload = async () => {
    if (!audioFile) {
      setError('先选择要改唱的原曲。');
      return;
    }
    if (!lrcFile && !lrcText.trim() && !autoTranscribe) {
      setError('请上传 LRC、粘贴带时间戳的歌词，或启用本地自动识别。');
      return;
    }
    setError('');
    setUploadProgress(0);
    try {
      const created = await uploadProject(audioFile, lrcFile, lrcText, autoTranscribe, selectedSingerId, setUploadProgress);
      localStorage.setItem(LOCAL_PROJECT_KEY, created.project_id);
      setShowHome(false);
      window.history.replaceState(null, '', `/?project=${encodeURIComponent(created.project_id)}&type=rewrite`);
      const next = await api.project(created.project_id);
      setProject(next);
      setSelectedIndex(0);
      setActiveJob(null);
      setUploadProgress(null);
      connectJob(created.job_id, created.project_id);
      setToast('工作区已创建，人声分离已进入后台队列。');
    } catch (cause) {
      setUploadProgress(null);
      setError(cause instanceof Error ? cause.message : '上传失败。');
    }
  };

  const setLineText = (index: number, value: string) => {
    setProject((current) => {
      if (!current) return current;
      const segments = current.segments.map((line, lineIndex) => lineIndex === index ? { ...line, edited: value } : line);
      return { ...current, segments };
    });
  };

  const commitLineTime = async (index: number, edge: 'start' | 'end', value: string) => {
    if (!project) return;
    const id = project.id;
    const key = timingDraftKey(id, index, edge);
    const clearDraft = () => setTimingDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    const nextTime = parseLrcTimestamp(value);
    if (nextTime === null || nextTime < 0 || nextTime >= project.duration) {
      setError('时间格式应为 分:秒.百分秒，例如 01:23.45。');
      clearDraft();
      return;
    }
    if (timingLocked) {
      setError('请等当前试听或整曲任务完成后再调整时间戳。');
      clearDraft();
      return;
    }

    const starts = project.segments.map((line) => line.start);
    const ends = project.segments.map((line) => line.end);
    if (edge === 'start') {
      const shift = nextTime - starts[index];
      starts[index] = nextTime;
      ends[index] = Math.max(nextTime + 0.3, ends[index] + shift);
      // Keep the edited phrase duration when its start moves, while moving
      // shared neighbor boundaries out of the way instead of rejecting it.
      if (index > 0 && ends[index - 1] > nextTime) ends[index - 1] = nextTime;
      if (index + 1 < starts.length && starts[index + 1] < ends[index]) {
        starts[index + 1] = ends[index];
      }
    } else {
      ends[index] = nextTime;
      if (index + 1 < starts.length && starts[index + 1] < nextTime) {
        starts[index + 1] = nextTime;
      }
    }
    const invalid = starts.some((start, lineIndex) =>
      start < 0 || (lineIndex > 0 && start - starts[lineIndex - 1] < 0.3) || ends[lineIndex] - start < 0.3,
    ) || ends.some((end, lineIndex) =>
      end > project.duration || (lineIndex + 1 < starts.length && end > starts[lineIndex + 1] + 0.01),
    );
    if (invalid) {
      setError('每句至少保留 0.30 秒；相邻句不能重叠。若邻句剩余时长不足，请缩小调整幅度。');
      clearDraft();
      return;
    }
    if (
      Math.abs(starts[index] - project.segments[index].start) < 0.005 &&
      Math.abs(ends[index] - project.segments[index].end) < 0.005
    ) {
      clearDraft();
      return;
    }

    setError('');
    setTimingSaving(true);
    try {
      const saved = await api.updateTimings(id, starts, ends);
      setProject((current) => {
        if (!current || current.id !== id) return current;
        const latestPreviews = { ...current.latest_previews };
        for (const invalidatedIndex of saved.invalidated_preview_indices) delete latestPreviews[invalidatedIndex];
        const segments = current.segments.map((line, lineIndex) => ({
          ...line,
          start: saved.starts[lineIndex],
          end: saved.ends[lineIndex],
        }));
        return {
          ...current,
          segments,
          latest_previews: latestPreviews,
          last_generation: saved.final_invalidated ? null : current.last_generation,
        };
      });
      if (saved.invalidated_preview_indices.includes(selectedIndex)) setComparison(null);
      const adjustedNeighbor = saved.invalidated_preview_indices.some((lineIndex) => lineIndex !== index);
      const timingMessage = adjustedNeighbor
        ? '时间已保存，并自动调整了相邻句边界。'
        : '句子起止时间已保存；原曲间奏会保留。';
      setToast(saved.final_invalidated ? `${timingMessage}旧试听和整曲已标记为过期。` : timingMessage);
      window.setTimeout(() => setToast(''), 4200);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '时间戳保存失败。');
    } finally {
      setTimingSaving(false);
      clearDraft();
    }
  };

  const playOriginal = (index: number) => {
    if (!project) return;
    setSelectedIndex(index);
    setComparison({
      index,
      mode: 'original',
      src: `/api/projects/${project.id}/lines/${index}/preview/original?t=${Date.now()}`,
      autoPlay: true,
    });
  };

  const requestLinePreview = async (index: number) => {
    if (!project) return;
    const line = project.segments[index];
    if (!line.edited.trim()) {
      setError('重唱歌词不能为空。');
      return;
    }
    setError('');
    setSelectedIndex(index);
    try {
      const result = await api.preview(project.id, index, line.edited, steps, pitchShift);
      setPreviewJobs((current) => ({ ...current, [index]: { id: result.job_id, project_id: project.id, parent_id: null, kind: 'preview', status: 'queued', progress: 0, label: '进入试听队列', error: null, result: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() } }));
      setComparison({ index, mode: 'generated', src: '', autoPlay: true });
      connectJob(result.job_id, project.id, index);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '试听任务提交失败。');
    }
  };

  const startGeneration = async () => {
    if (!project) return;
    setError('');
    try {
      const result = await api.generate(
        project.id,
        project.segments.map((line, index) => ({ index, text: line.edited })),
        steps,
        pitchShift,
      );
      setActiveJob({ id: result.job_id, project_id: project.id, parent_id: null, kind: 'generate_song', status: 'queued', progress: 0, label: `等待 GPU 队列 · 0/${result.changed_lines} 句`, error: null, result: null, line_count: result.changed_lines, lines_done: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      connectJob(result.job_id, project.id);
      setToast(`已排入 ${result.changed_lines} 句重唱任务。试听请求会优先调度。`);
      window.setTimeout(() => setToast(''), 4200);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '整曲任务提交失败。');
    }
  };

  const cancelGeneration = async () => {
    if (!activeJob) return;
    try {
      const next = await api.cancel(activeJob.id);
      setActiveJob(next);
      setToast('已提交停止请求；正在运行的句子会安全结束后停止。');
      window.setTimeout(() => setToast(''), 4200);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法取消任务。');
    }
  };

  const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) setAudioFile(file);
  };

  const previewJob = selectedIndex in previewJobs ? previewJobs[selectedIndex] : undefined;
  const hasGeneratedSong = Boolean(project?.last_generation);

  return (
    <div className="studio-shell">
      <aside className={`rail ${collapsed ? 'rail-collapsed' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark"><AudioLines size={22} strokeWidth={2.2} /></div>
          {!collapsed && <div className="brand-copy"><strong>SONG <span>OF THESEUS</span></strong><small>LOCAL VOCAL LAB</small></div>}
          <button className="icon-button rail-toggle" onClick={() => setCollapsed((value) => !value)} aria-label="折叠导航"><ChevronDown size={15} /></button>
        </div>

        {!collapsed && <>
          <div className="rail-section-label">工作区</div>
          <button className={`nav-item ${showHome ? 'nav-active' : ''}`} onClick={goHome}><Home size={17} /><span>工作区首页</span>{showHome && <span className="nav-dot" />}</button>
          <button className={`nav-item ${!showHome && workspace === 'rewrite' ? 'nav-active' : ''}`} onClick={() => { setWorkspace('rewrite'); setShowHome(false); }}><Music2 size={17} /><span>逐句改唱</span>{!showHome && workspace === 'rewrite' && <span className="nav-dot" />}</button>
          <button className={`nav-item ${!showHome && workspace === 'midi' ? 'nav-active' : ''}`} onClick={() => { setWorkspace('midi'); setShowHome(false); }}><FileMusic size={17} /><span>MIDI 填词</span>{!showHome && workspace === 'midi' && <span className="nav-dot" />}</button>
          <button className="nav-item" onClick={() => document.querySelector('.line-workspace')?.scrollIntoView({ behavior: 'smooth' })}><AudioLines size={17} /><span>歌词时间轴</span></button>
          <button className="nav-item" onClick={() => document.querySelector('.queue-panel')?.scrollIntoView({ behavior: 'smooth' })}><Activity size={17} /><span>任务队列</span>{queue.some((job) => job.status === 'queued' || job.status === 'running') && <i />}</button>
          <button className="nav-item" onClick={openSingerManager}><Mic2 size={17} /><span>歌手管理</span><span className="nav-count">{singers.length}</span></button>

          <div className="rail-section-label rail-gap">本地引擎</div>
          <div className="gpu-card">
            <div className="gpu-top"><span className="engine-pulse"><span /></span><span>GPU ENGINE</span><span className="engine-state">{health?.online ? 'ONLINE' : 'CONNECTING'}</span></div>
            <div className="gpu-name"><Cpu size={17} />{health?.gpu_name || '正在连接本地服务'}</div>
            <div className="gpu-stats"><span>设备</span><b>{health?.device?.toUpperCase() || '—'}</b></div>
            <div className="gpu-stats"><span>队列</span><b>{health ? `${health.queued_tasks} 等待` : '—'}</b></div>
            <div className="gpu-bar"><span style={{ width: `${health?.gpu_total_gib && health.gpu_free_gib !== null ? Math.max(5, (1 - health.gpu_free_gib / health.gpu_total_gib) * 100) : 5}%` }} /></div>
            <div className="gpu-foot"><span>显存占用</span><span>{health?.gpu_total_gib && health.gpu_free_gib !== null ? `${(health.gpu_total_gib - health.gpu_free_gib).toFixed(1)} / ${health.gpu_total_gib} GB` : '等待采样'}</span></div>
          </div>

          <div className="queue-panel">
            <div className="rail-section-head"><div className="rail-section-label">最近任务</div><span>{queue.length.toString().padStart(2, '0')}</span></div>
            {queue.length === 0 ? <div className="queue-empty">上传一首歌，队列会显示在这里。</div> : queue.slice(0, 5).map((job) => (
              <div className="queue-item" key={job.id}>
                <div className={`queue-icon ${job.status}`}>
                  {job.status === 'running' ? <LoaderCircle size={13} className="spin" /> : job.status === 'succeeded' ? <Check size={13} /> : job.kind === 'preview' ? <Headphones size={13} /> : <WandSparkles size={13} />}
                </div>
                <div className="queue-meta"><b>{job.kind === 'prepare' ? '人声分离' : job.kind === 'preview' ? '逐句试听' : job.kind === 'generate_song' ? '整曲重唱' : job.kind === 'midi_generate_song' ? 'MIDI 整曲生成' : job.kind}</b><small>{statusLabel(job.status)}</small></div>
                {job.status === 'running' && <span className="queue-live" />}
              </div>
            ))}
          </div>
          <div className="rail-bottom">
            <button className="nav-item" onClick={() => setToast('没有 LRC 时可使用本机 Whisper large-v3 识别歌词，识别结果需要校对。')}><CircleHelp size={17} /><span>使用说明</span></button>
            <div className="rail-version"><span className="version-wave"><AudioLines size={14} /></span><div><b>Song of Theseus</b><small>LOCAL SINGING WORKSTATION</small></div></div>
          </div>
        </>}
        {collapsed && <div className="collapsed-nav"><button className={`nav-item ${showHome ? 'nav-active' : ''}`} aria-label="工作区首页" onClick={goHome}><Home size={18} /></button><button className={`nav-item ${!showHome && workspace === 'rewrite' ? 'nav-active' : ''}`} aria-label="逐句改唱" onClick={() => { setWorkspace('rewrite'); setShowHome(false); }}><Music2 size={18} /></button><button className={`nav-item ${!showHome && workspace === 'midi' ? 'nav-active' : ''}`} aria-label="MIDI 填词" onClick={() => { setWorkspace('midi'); setShowHome(false); }}><FileMusic size={18} /></button><button className="nav-item" aria-label="歌手管理" onClick={openSingerManager}><Mic2 size={18} /></button><button className="nav-item" aria-label="任务队列"><Activity size={18} /></button></div>}
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-leading"><button className="home-shortcut" onClick={goHome} aria-label="返回工作区首页"><Home size={14} /></button><div className="breadcrumb"><span>SONG OF THESEUS</span><ChevronRight size={13} /><b>{showHome ? '工作区中心' : workspace === 'midi' ? 'MIDI 填词' : '逐句改唱'}</b></div></div>
          <div className="topbar-right"><span className="local-badge"><span />本地运行 · Powered by Stability AI</span><span className="top-divider" /><span className="build-badge">V1.0 <Sparkles size={12} /></span></div>
        </header>

        <div className="content-wrap">
          {showHome ? <WorkspaceHome workspaces={workspaces} loading={workspacesLoading} error={workspacesError} onRefresh={() => void refreshWorkspaces()} onNewRewrite={startNewProject} onNewMidi={startNewMidiProject} /> : workspace === 'midi' ? <MidiStudio singers={singers} selectedSingerId={selectedSingerId} onSingerChange={chooseSinger} onOpenSingerManager={openSingerManager} refreshToken={midiProjectRefreshToken} initialProjectId={initialMidiProjectId} /> : <>
          <section className="page-hero">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" />SING IT YOUR WAY <span className="eyebrow-mono">/ 逐句重构</span></div>
              <h1>让每一句，<em>重新开口。</em></h1>
              <p>上传原曲，逐句改写歌词。试听对比之后，再把改唱拼成一首完整的歌。</p>
            </div>
            <div className="hero-art" aria-hidden="true"><div className="hero-ring ring-one" /><div className="hero-ring ring-two" /><div className="hero-core"><AudioLines size={26} /></div><span className="hero-orbit orbit-a" /><span className="hero-orbit orbit-b" /><span className="hero-orbit orbit-c" /></div>
          </section>

          {error && <div className="alert alert-error"><X size={17} /><span>{error}</span><button onClick={() => setError('')}><X size={15} /></button></div>}
          {toast && <div className="toast"><Check size={16} />{toast}<button onClick={() => setToast('')} aria-label="关闭"><X size={14} /></button></div>}

          {!project || project.status === 'failed' ? (
            <section className="upload-grid">
              <div className="panel upload-panel">
                <div className="panel-heading"><div><div className="step-chip">01 <span>INPUT</span></div><h2>建立你的改唱工作区</h2><p>先加入原曲和对齐歌词，剩下的交给本地引擎。</p></div><span className="panel-index">01 / 03</span></div>
                <div className="singer-select-row"><div className="singer-select-copy"><span className="singer-select-icon"><Mic2 size={16} /></span><div><b>重唱歌手</b><small>{selectedSingerId ? '此歌手会用于本工程的全部重唱片段' : '默认从原曲提取歌手音色'}</small></div></div><select aria-label="新工程重唱歌手" value={selectedSingerId} onChange={(event) => chooseSinger(event.target.value)}><option value="">沿用原曲歌手</option>{singers.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select><button className="button button-quiet" onClick={openSingerManager}><Pencil size={13} />管理</button></div>
                <div className={`dropzone ${isDragging ? 'dropzone-active' : ''} ${audioFile ? 'dropzone-loaded' : ''}`} onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }} onDragLeave={() => setIsDragging(false)} onDrop={onDrop} onClick={() => fileInput.current?.click()}>
                  <input ref={fileInput} type="file" accept=".wav,.mp3,.flac,.ogg,audio/*" hidden onChange={(event) => setAudioFile(event.target.files?.[0] || null)} />
                  <div className="drop-icon">{audioFile ? <FileAudio size={22} /> : <Upload size={22} />}</div>
                  <div className="drop-copy"><b>{audioFile ? audioFile.name : '把原曲拖到这里，或点击选择'}</b><span>{audioFile ? `${(audioFile.size / (1024 * 1024)).toFixed(1)} MB · 已就绪` : '支持 WAV / MP3 / FLAC / OGG · 最长 10 分钟'}</span></div>
                  {audioFile ? <button className="file-clear" onClick={(event) => { event.stopPropagation(); setAudioFile(null); }} aria-label="移除音频"><X size={15} /></button> : <span className="drop-shortcut">BROWSE</span>}
                </div>
                <input ref={lrcInput} type="file" accept=".lrc,.txt,text/plain" hidden onChange={(event) => setLrcFile(event.target.files?.[0] || null)} />
                  <div className={`lrc-entry ${lrcFile ? 'lrc-loaded' : ''}`}>
                  <div className="lrc-entry-icon"><FileText size={19} /></div>
                  <div className="lrc-entry-copy"><b>{lrcFile ? lrcFile.name : '时间戳歌词 LRC'}</b><small>{lrcFile ? '文件已选择，上传时会读取' : '有 LRC 就直接使用，也可以让本机识别歌词'}</small></div>
                  <button className="button button-quiet" onClick={() => lrcInput.current?.click()}>{lrcFile ? '更换文件' : '选择文件'}</button>
                  <button className="paste-toggle" onClick={() => setShowLrcPaste((value) => !value)}>{showLrcPaste ? '收起粘贴框' : '粘贴文本'}<ChevronDown size={14} className={showLrcPaste ? 'rotate' : ''} /></button>
                </div>
                {showLrcPaste && <textarea className="lrc-textarea" value={lrcText} onChange={(event) => setLrcText(event.target.value)} placeholder={'[00:12.30]第一句歌词\n[00:15.80]第二句歌词'} rows={6} />}
                <label className="auto-transcribe-option">
                  <input type="checkbox" checked={autoTranscribe} onChange={(event) => setAutoTranscribe(event.target.checked)} />
                  <span className="auto-transcribe-copy"><b>没有 LRC 时自动识别歌词</b><small>Whisper large-v3 · 本机运行 · 歌曲识别需校对</small></span>
                  <span className={`auto-transcribe-state ${autoTranscribe ? 'enabled' : ''}`}>{autoTranscribe ? 'ON' : 'OFF'}</span>
                </label>
                <div className="upload-footer"><div className="privacy-note"><Radio size={14} />所有音频都在这台电脑上处理</div><button className="button button-primary" disabled={uploadProgress !== null} onClick={onUpload}>{uploadProgress !== null ? <><LoaderCircle className="spin" size={16} />上传中 {uploadProgress}%</> : <>创建工作区<ChevronRight size={16} /></>}</button></div>
                {uploadProgress !== null && <div className="upload-progress"><span style={{ width: `${uploadProgress}%` }} /></div>}
              </div>
              <div className="panel explainer-panel">
                <div className="step-chip">FLOW <span>3 STEPS</span></div>
                <h3>从原曲，到<br /><em>全新的演绎。</em></h3>
                <div className="flow-list"><div className="flow-step"><span className="flow-number">01</span><span className="flow-connector" /><div><b>拆分音轨</b><small>分离原唱人声与伴奏</small></div><AudioLines size={17} /></div><div className="flow-step"><span className="flow-number">02</span><span className="flow-connector" /><div><b>逐句重唱</b><small>修改歌词，试听新声线</small></div><Headphones size={17} /></div><div className="flow-step"><span className="flow-number">03</span><div><b>合成整曲</b><small>将重唱与原伴奏拼接</small></div><Sparkles size={17} /></div></div>
                <div className="explainer-foot"><span className="tiny-wave">▂ ▄ ▆ ▃ ▇ ▄ ▂ ▅ ▃ ▇ ▄ ▂ ▆ ▃ ▅</span><span>YINGMUSIC · LOCAL INFERENCE</span></div>
              </div>
            </section>
          ) : (
            <>
              {project.status === 'preparing' && <JobBanner job={activeJob} onCancel={cancelGeneration} />}
              <section className="source-strip panel">
                <div className="source-cover"><div className="cover-orbit" /><AudioLines size={21} /></div>
                <div className="source-title"><span className="micro-label">SOURCE TRACK <span className="source-live"><i /> READY</span></span><b>原曲与分离人声</b><small>{formatClock(project.duration)} · {project.segments.length} 句歌词 · 44.1 kHz</small><div className="project-singer-picker"><Mic2 size={12} /><select aria-label="当前工程歌手" value={project.singer_profile?.id || ''} onChange={(event) => void applyProjectSinger(event.target.value)} disabled={project.status !== 'ready' || projectSingerSaving || generationBusy || previewBusy}><option value="">原曲歌手</option>{project.singer_profile && !singers.some((item) => item.id === project.singer_profile?.id) && <option value={project.singer_profile.id}>{project.singer_profile.name}（已从歌手库移除）</option>}{singers.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select>{projectSingerSaving && <LoaderCircle size={12} className="spin" />}</div></div>
                <div className="source-audio source-audio-main"><span>原曲</span><AudioPlayer src={projectAsset(project.id, 'original')} durationHint={project.duration} label="原曲" variant="source" /></div>
                {project.status === 'ready' && <div className="source-audio source-audio-vocal"><span>原唱人声</span><AudioPlayer src={projectAsset(project.id, 'vocals')} durationHint={project.duration} label="原唱人声" variant="source" /></div>}
                <button className="button button-quiet reset-button" onClick={startNewProject}><Plus size={15} />新建</button>
              </section>

              {project.status !== 'ready' ? (
                <section className="panel waiting-panel"><div className="waiting-icon"><LoaderCircle size={22} className="spin" /></div><div><b>正在准备音轨</b><p>{activeJob?.label || '任务已进入本地队列，页面仍可继续浏览。'}</p></div><JobMeter job={activeJob} /></section>
              ) : (
                <section className="edit-layout">
                  <div className="edit-main">
                    <div className="panel timeline-panel">
                      <div className="timeline-topline"><div><div className="step-chip">02 <span>EDIT & LISTEN</span></div><h2>逐句编辑你的歌词</h2><p>保留原句就无需改动；点击「听原唱」或「重唱试听」做 A/B 对比。</p><p className="timing-note">每句起唱和句尾可分别校准；需要时会自动调整相邻句边界，句间空白保留原曲伴奏。</p>{project.lyrics_source?.startsWith('whisper-') && <p className="transcription-note"><Sparkles size={11} />本机识别的歌词初稿，歌曲可能听错；请逐句校对。</p>}</div><div className="edit-count"><b>{changedCount.toString().padStart(2, '0')}</b><span>句已修改</span></div></div>
                      <div className="timeline-tools"><div className="timeline-legend"><span className="legend-original"><i />原始歌词</span><span className="legend-edited"><i />改写歌词</span></div><div className="timeline-count">{project.segments.length} LINES <span>·</span> 排序按 LRC 时间戳</div></div>
                      <div className="line-list" id="line-workspace">
                        {project.segments.map((line, index) => {
                          const edited = line.original.trim() !== line.edited.trim();
                          const lineJob = previewJobs[index];
                          const lineBusy = lineJob?.status === 'queued' || lineJob?.status === 'running';
                          return <div className={`line-row ${selectedIndex === index ? 'line-selected' : ''} ${edited ? 'line-edited' : ''}`} key={`${line.start}-${index}`} onClick={() => setSelectedIndex(index)}>
                            <div className="line-number">{(index + 1).toString().padStart(2, '0')}</div>
                            <div className="line-time">
                              <input className="line-time-input" type="text" value={timingDrafts[timingDraftKey(project.id, index, 'start')] ?? formatLrcTimestamp(line.start)} onChange={(event) => setTimingDrafts((current) => ({ ...current, [timingDraftKey(project.id, index, 'start')]: event.target.value }))} onBlur={(event) => void commitLineTime(index, 'start', event.currentTarget.value)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }} onClick={(event) => event.stopPropagation()} disabled={timingLocked} title="编辑此句起唱时间；必要时自动调整相邻句边界" aria-label={`第 ${index + 1} 句起唱时间，格式分:秒.百分秒`} />
                              <span>→</span>
                              <input className="line-time-input line-time-end" type="text" value={timingDrafts[timingDraftKey(project.id, index, 'end')] ?? formatLrcTimestamp(line.end)} onChange={(event) => setTimingDrafts((current) => ({ ...current, [timingDraftKey(project.id, index, 'end')]: event.target.value }))} onBlur={(event) => void commitLineTime(index, 'end', event.currentTarget.value)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }} onClick={(event) => event.stopPropagation()} disabled={timingLocked} title="编辑此句句尾；延长时会自动后移下一句起唱点" aria-label={`第 ${index + 1} 句句尾时间，格式分:秒.百分秒`} />
                            </div>
                            <div className="line-content"><div className="line-original"><span>原</span><p>{line.original}</p><button className="listen-button" onClick={(event) => { event.stopPropagation(); playOriginal(index); }}><Play size={11} fill="currentColor" />听原唱</button></div><div className="line-edit"><span>新</span><input value={line.edited} onChange={(event) => setLineText(index, event.target.value)} onClick={(event) => event.stopPropagation()} aria-label={`第 ${index + 1} 句修改后歌词`} placeholder="输入新的歌词" /><button className={`listen-button listen-new ${lineBusy ? 'is-busy' : ''}`} disabled={lineBusy || project.status !== 'ready' || timingSaving} title="重新生成这句并在完成后播放" onClick={(event) => { event.stopPropagation(); void requestLinePreview(index); }}>{lineBusy ? <LoaderCircle size={12} className="spin" /> : <Play size={11} fill="currentColor" />}{lineBusy ? '生成中' : '替换试听'}</button></div></div>
                            <div className={`line-state ${edited ? 'state-changed' : ''}`}>{edited ? <WandSparkles size={14} /> : <span className="state-dash" />}</div>
                          </div>;
                        })}
                      </div>
                      <div className="timeline-footer"><span>已滚动到底部？{project.segments.length} 句均可试听</span><button className="text-button" onClick={() => { if (!project) return; setProject({ ...project, segments: project.segments.map((line) => ({ ...line, edited: line.original })) }); }}><RefreshCw size={13} />恢复全部原词</button></div>
                    </div>

                    <section className="panel render-panel">
                      <div className="render-copy"><div className="step-chip">03 <span>FINAL RENDER</span></div><h2>把修改拼成一首歌</h2><p>原曲伴奏贯穿全曲，未改动的人声保留原唱。</p></div>
                      <div className="render-controls"><label htmlFor="step-range"><span>扩散步数</span><b>{steps}<small> / 64</small></b></label><input id="step-range" type="range" min="8" max="64" step="4" value={steps} onChange={(event) => setSteps(Number(event.target.value))} /><div className="range-captions"><span>更快</span><span>越高越慢，细节计算越多</span><span>更细</span></div></div>
                      <div className="render-action">{activeJob?.kind === 'generate_song' && (activeJob.status === 'queued' || activeJob.status === 'running') ? <button className="button button-danger" onClick={cancelGeneration}><Square size={15} fill="currentColor" />停止整曲任务</button> : <button className="button button-primary button-render" onClick={() => void startGeneration()} disabled={changedCount === 0 || timingSaving}><WandSparkles size={17} />开始重唱并拼接<span>{changedCount} 句</span></button>}</div>
                    </section>

                    {activeJob?.kind === 'generate_song' && <JobBanner job={activeJob} onCancel={cancelGeneration} />}
                    {hasGeneratedSong && <section className="panel final-panel"><div className="final-icon"><Check size={19} /></div><div className="final-title"><span className="micro-label">FINAL MASTER · {project.last_generation?.steps} STEPS</span><b>整曲已完成</b><small>{project.last_generation?.changed_lines} 句重唱{project.last_generation?.pitch_shift ? ` · 旋律 ${project.last_generation.pitch_shift > 0 ? '+' : ''}${project.last_generation.pitch_shift} 半音` : ''} · WAV / 44.1 kHz</small></div><AudioPlayer src={`${projectAsset(project.id, 'final')}?v=${project.last_generation?.created_at}`} durationHint={project.duration} label="整曲成品" variant="master" /><a className="button button-download" href={projectAsset(project.id, 'final')} download><Download size={16} />下载 WAV</a></section>}
                  </div>

                  <aside className="inspector">
                    <div className="panel inspector-panel">
                      <div className="inspector-heading"><div><div className="step-chip">LISTEN BACK</div><h3>对比试听</h3></div><span className="headphone-glow"><Headphones size={19} /></span></div>
                      {selectedLine ? <>
                        <div className="selected-line-card"><span className="micro-label">LINE {(selectedIndex + 1).toString().padStart(2, '0')} · {formatClock(selectedLine.start)}—{formatClock(selectedLine.end)}</span><b>{selectedLine.edited || '未填写歌词'}</b><small>原句：{selectedLine.original}</small></div>
                        <div className="compare-switch"><button className={comparison?.mode !== 'generated' ? 'compare-active' : ''} onClick={() => playOriginal(selectedIndex)}><AudioLines size={14} />原唱</button><button className={comparison?.mode === 'generated' ? 'compare-active compare-new' : ''} onClick={() => void requestLinePreview(selectedIndex)} disabled={previewJob?.status === 'queued' || previewJob?.status === 'running'} title="重新生成这句并在完成后播放"><WandSparkles size={14} />替换试听{previewJob?.status === 'running' || previewJob?.status === 'queued' ? <LoaderCircle size={13} className="spin" /> : null}</button></div>
                        <div className="player-card"><div className={`player-art ${comparison?.mode === 'generated' ? 'player-art-new' : ''}`}><div className="player-eq">{Array.from({ length: 20 }, (_, index) => <i key={index} style={{ height: `${12 + ((index * 17 + selectedIndex * 7) % 31)}px` }} />)}</div><span>{comparison?.mode === 'generated' ? 'RE-SUNG TAKE' : 'ORIGINAL TAKE'}</span></div>{comparison?.src ? <AudioPlayer key={comparison.src} src={comparison.src} durationHint={selectedLine.end - selectedLine.start} label={comparison.mode === 'generated' ? '重唱试听' : '原唱试听'} variant="inspector" autoPlay={Boolean(comparison.autoPlay)} /> : <div className="player-wait">{previewJob?.status === 'running' || previewJob?.status === 'queued' ? <><LoaderCircle size={15} className="spin" />{previewJob.label}</> : <>点上方按钮开始试听</>}</div>}</div>
                        <div className="pitch-control">
                          <label htmlFor="pitch-shift-range"><span>重唱旋律移调</span><b>{pitchShift > 0 ? `+${pitchShift}` : pitchShift}<small> 半音</small></b></label>
                          <input id="pitch-shift-range" type="range" min="-12" max="12" step="1" value={pitchShift} onChange={(event) => setPitchShift(Number(event.target.value))} disabled={generationBusy || previewBusy} />
                          <div className="range-captions"><span>−12</span><span>低一个八度可试 +12</span><span>+12</span></div>
                          <small>只调整生成旋律，原曲伴奏不变；试听与整曲共用此设置。</small>
                        </div>
                        <div className="inspector-help"><span className="help-dot">i</span>{comparison?.mode === 'generated' ? '替换试听会重新生成这一句，完成后自动播放。' : '点击原唱后直接播放源文件中的对应句。'}</div>
                        {previewJob && <div className={`preview-status ${previewJob.status}`}><span>{statusLabel(previewJob.status)}</span><small>{previewJob.label}</small>{previewJob.status === 'failed' && <small>{previewJob.error}</small>}</div>}
                      </> : <div className="inspector-empty"><Headphones size={26} /><b>选一句歌词开始试听</b><span>逐句听原唱与重唱的差异</span></div>}
                    </div>
                    <div className="panel queue-card queue-panel"><div className="queue-card-head"><div><div className="step-chip">SCHEDULER</div><h3>GPU 任务队列</h3></div><span className="worker-pill"><i />1 WORKER</span></div><p>试听优先 · 整曲分句排队</p><div className="queue-list">{queue.filter((job) => !job.parent_id).slice(0, 5).map((job) => <div className="queue-task" key={job.id}><span className={`queue-task-icon ${job.status}`}>{job.status === 'running' ? <LoaderCircle size={13} className="spin" /> : job.status === 'succeeded' ? <Check size={13} /> : <Activity size={13} />}</span><div><b>{job.kind === 'prepare' ? '人声分离' : job.kind === 'preview' ? '重唱试听' : job.kind === 'generate_song' ? '整曲渲染' : job.kind === 'midi_generate_song' ? 'MIDI 整曲生成' : job.kind}</b><small>{job.label}</small></div><span className={`queue-state state-${job.status}`}>{statusLabel(job.status)}</span></div>)}</div><div className="queue-rule"><span>CUDA</span><i /><span>单路串行，避免显存抢占</span></div></div>
                    <div className="tip-card"><Sparkles size={15} /><div><b>小提示</b><span>先用 32 步试听确认节奏和咬字，再将整曲步数调高。64 步耗时最长。</span></div></div>
                  </aside>
                </section>
              )}
            </>
          )}
      <footer className="page-footer"><span>SONG OF THESEUS <i>·</i> YOUR VOICE, YOUR CUT</span><span>PROCESSING RUNS LOCALLY <span className="footer-pulse" /></span></footer>
          </>}
        </div>
      </main>
      {showSingerManager && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowSingerManager(false); }}>
        <section className="panel singer-modal" role="dialog" aria-modal="true" aria-label="歌手管理">
          <div className="singer-modal-heading"><div><div className="step-chip"><Mic2 size={13} /> VOICE LIBRARY</div><h2>歌手管理</h2><p>保存常用音色参考；选用后，工程里的每句都会复用同一位歌手。</p></div><button className="icon-button" aria-label="关闭歌手管理" onClick={() => setShowSingerManager(false)}><X size={16} /></button></div>
          {singerError && <div className="alert alert-error singer-modal-error"><X size={15} /><span>{singerError}</span><button onClick={() => setSingerError('')} aria-label="关闭错误"><X size={13} /></button></div>}
          <div className="singer-manager-layout">
            <form className="singer-form" onSubmit={(event) => { event.preventDefault(); void saveSinger(); }}>
              <div className="singer-form-title"><b>{editingSingerId ? '编辑歌手资料' : '添加歌手'}</b>{editingSingerId && <button type="button" className="text-button" onClick={resetSingerForm}>取消编辑</button>}</div>
              <label>歌手名称<input value={singerName} maxLength={50} onChange={(event) => setSingerName(event.target.value)} placeholder="例如：主唱 A" /></label>
              <label>参考音频中的歌词<textarea value={singerReferenceText} maxLength={500} onChange={(event) => setSingerReferenceText(event.target.value)} placeholder="请填写参考音频里实际唱出的歌词" rows={3} /></label>
              <div className="singer-audio-field"><span>清晰的独唱参考音频</span><small>{editingSingerId ? '不选新文件则保留原音频' : '建议 5–7 秒，接受 3–20 秒'}</small><input ref={singerAudioInput} type="file" accept=".wav,.mp3,.flac,.ogg,audio/*" onChange={(event) => setSingerAudioFile(event.target.files?.[0] || null)} /><small>{singerAudioFile ? singerAudioFile.name : editingSingerId ? '未选择替换文件' : 'WAV / MP3 / FLAC / OGG'}</small></div>
              <button className="button button-primary singer-save-button" type="submit" disabled={singerSaving}>{singerSaving ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}{singerSaving ? '保存中…' : '保存歌手资料'}</button>
              <p className="singer-form-tip">歌手资料仅保存在本机。工程选用后会保存一份独立副本，后续片段固定使用它。</p>
            </form>
            <div className="singer-library-list">
              <div className="singer-list-heading"><b>已保存的歌手</b><span>{singers.length} 位</span></div>
              {singers.length === 0 ? <div className="singer-library-empty"><Mic2 size={24} /><b>还没有歌手资料</b><span>添加参考音频后，即可在新工程中快捷选择。</span></div> : singers.map((profile) => {
                const selected = workspace === 'rewrite' && project ? project.singer_profile?.id === profile.id : selectedSingerId === profile.id;
                const needsProjectSync = Boolean(workspace === 'rewrite' && project && selected && project.singer_profile?.updated_at !== profile.updated_at);
                return <article className={`singer-profile-card ${selected ? 'singer-profile-selected' : ''}`} key={profile.id}>
                  <div className="singer-profile-heading"><div><b>{profile.name}</b><span>{profile.duration.toFixed(1)} 秒</span></div>{selected && <small><Check size={11} />当前选用</small>}</div>
                  <p>{profile.reference_text}</p>
                  <AudioPlayer src={profile.audio_url} durationHint={profile.duration} label={`${profile.name}参考人声`} variant="inspector" />
                  <div className="singer-profile-actions"><button className="button button-quiet" onClick={() => void selectSingerFromLibrary(profile)} disabled={projectSingerSaving}>{needsProjectSync ? '同步到工程' : selected ? '已选用' : '选用'}</button><button className="icon-button" aria-label={`编辑${profile.name}`} onClick={() => editSinger(profile)}><Pencil size={14} /></button><button className="icon-button singer-delete-button" aria-label={`删除${profile.name}`} onClick={() => void removeSinger(profile)}><Trash2 size={14} /></button></div>
                </article>;
              })}
            </div>
          </div>
        </section>
      </div>}
    </div>
  );
}

function JobMeter({ job }: { job: Job | null }) {
  if (!job) return <div className="meter-placeholder"><span />后台工作线程</div>;
  return <div className="job-meter"><div><span>{job.label}</span><b>{Math.round(job.progress * 100)}%</b></div><div className="progress-track"><span style={{ width: `${Math.max(3, job.progress * 100)}%` }} /></div></div>;
}

function JobBanner({ job, onCancel }: { job: Job | null; onCancel: () => void }) {
  if (!job) return null;
  const isBusy = job.status === 'queued' || job.status === 'running';
  return <section className={`panel job-banner ${isBusy ? 'job-working' : job.status}`}><div className="job-banner-icon">{job.status === 'succeeded' ? <Check size={19} /> : job.status === 'failed' ? <X size={19} /> : <LoaderCircle size={19} className={isBusy ? 'spin' : ''} />}</div><div className="job-banner-copy"><span className="micro-label">{job.kind === 'prepare' ? 'AUDIO PREPARATION' : 'SONG RENDER QUEUE'} · {statusLabel(job.status)}</span><b>{job.label}</b>{job.error && <small>{job.error}</small>}</div>{job.line_count && <div className="job-line-count"><b>{job.lines_done ?? 0}<small>/{job.line_count}</small></b><span>句</span></div>}<JobMeter job={job} />{isBusy && job.kind === 'generate_song' && <button className="icon-button cancel-job" onClick={onCancel} title="停止任务"><Square size={14} /></button>}</section>;
}

function formatAudioTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00';
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0');
  const remainder = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${remainder}`;
}

function AudioPlayer({
  src,
  durationHint = 0,
  label,
  variant,
  autoPlay = false,
}: {
  src: string;
  durationHint?: number;
  label: string;
  variant: 'source' | 'master' | 'inspector';
  autoPlay?: boolean;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [actualDuration, setActualDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const duration = actualDuration || durationHint;

  useEffect(() => {
    setPlaying(false);
    setCurrentTime(0);
    setActualDuration(0);
    setLoadError(false);
  }, [src]);

  useEffect(() => {
    if (!autoPlay) return;
    const audio = audioRef.current;
    if (!audio) return;
    let cancelled = false;
    void audio.play().then(() => {
      if (!cancelled) setLoadError(false);
    }).catch(() => {
      if (!cancelled) setLoadError(true);
    });
    return () => { cancelled = true; };
  }, [autoPlay, src]);

  const togglePlayback = async () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      try {
        await audio.play();
        setLoadError(false);
      } catch {
        setLoadError(true);
      }
    } else {
      audio.pause();
    }
  };

  const seek = (event: React.ChangeEvent<HTMLInputElement>) => {
    const audio = audioRef.current;
    const nextTime = Number(event.currentTarget.value);
    if (audio && Number.isFinite(nextTime)) {
      audio.currentTime = nextTime;
      setCurrentTime(nextTime);
    }
  };

  const toggleMute = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.muted = !audio.muted;
    setMuted(audio.muted);
  };

  return (
    <div className={`audio-player audio-player-${variant}`} role="group" aria-label={label}>
      <audio
        ref={audioRef}
        className="audio-element"
        src={src}
        preload="none"
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => {
          setActualDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
          setLoadError(false);
        }}
        onDurationChange={(event) => setActualDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => setLoadError(true)}
      />
      <button className="audio-play" type="button" aria-label={playing ? `暂停${label}` : `播放${label}`} onClick={() => void togglePlayback()}>
        {playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
      </button>
      <span className="audio-clock">{formatAudioTime(currentTime)}</span>
      <input
        className="audio-seek"
        type="range"
        min={0}
        max={Math.max(duration, 0.1)}
        step={0.1}
        value={Math.min(currentTime, Math.max(duration, 0.1))}
        style={{ '--audio-progress': `${duration ? Math.min(100, (currentTime / duration) * 100) : 0}%` } as React.CSSProperties}
        aria-label={`${label}播放进度`}
        onChange={seek}
      />
      <span className="audio-clock audio-total">{formatAudioTime(duration)}</span>
      <button className={`audio-mute ${muted ? 'is-muted' : ''}`} type="button" aria-label={muted ? `取消静音${label}` : `静音${label}`} onClick={toggleMute}>
        {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
      </button>
      {loadError && <span className="audio-error">音频加载失败</span>}
    </div>
  );
}

export default App;
