import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AudioLines, Check, ChevronRight, Download, FileMusic, LoaderCircle,
  Mic2, Music2, Play, Plus, Radio, Sparkles, Square, Upload, WandSparkles, X,
} from 'lucide-react';
import { api, uploadMidiProject, watchJob } from './api';
import type { Job, MidiLyricLine, MidiLyricLinePayload, MidiNote, MidiProject, MidiSection, SingerProfile } from './types';

const LAST_MIDI_PROJECT = 'yingstudio:last-midi-project';
const MIDI_DRAFT_PREFIX = 'yingstudio:midi-lyrics:';
const MIDI_LRC_DRAFT_PREFIX = 'yingstudio:midi-lrc:';

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0');
  const remainder = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${remainder}`;
}

function formatMidiTimestamp(seconds: number) {
  const totalCentiseconds = Math.max(0, Math.round(seconds * 100));
  const minutes = Math.floor(totalCentiseconds / 6000).toString().padStart(2, '0');
  const remainder = ((totalCentiseconds % 6000) / 100).toFixed(2).padStart(5, '0');
  return `${minutes}:${remainder}`;
}

function parseMidiTimestamp(value: string) {
  const match = value.trim().match(/^(\d{1,3}):([0-5]?\d)(?:[.:](\d{1,3}))?$/);
  if (!match) return null;
  const fraction = match[3] ? Number(`0.${match[3]}`) : 0;
  return Math.round((Number(match[1]) * 60 + Number(match[2]) + fraction) * 100) / 100;
}

function restoreDraft(project: MidiProject) {
  try {
    const raw = localStorage.getItem(`${MIDI_DRAFT_PREFIX}${project.id}`);
    const draft = raw ? JSON.parse(raw) as unknown : null;
    if (!Array.isArray(draft)) return project;
    return {
      ...project,
      segments: project.segments.map((section, index) => ({
        ...section,
        edited: typeof draft[index] === 'string' ? draft[index] : section.edited,
      })),
    };
  } catch {
    localStorage.removeItem(`${MIDI_DRAFT_PREFIX}${project.id}`);
    return project;
  }
}

function parseMidiLrc(source: string): MidiLyricLine[] {
  const rows: string[] = [];
  for (const rawLine of source.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const matches = [...rawLine.matchAll(/\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g)];
    if (!matches.length) continue;
    const text = rawLine
      .replace(/\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g, '')
      .replace(/\[[a-z]+:[^\]]*\]/gi, '')
      .trim();
    if (!text) continue;
    // Timestamps are deliberately ignored: LRC supplies lyric order and phrase breaks only.
    for (let index = 0; index < matches.length; index += 1) rows.push(text);
  }
  return rows.map((text, index) => ({
    id: `lrc-${index}`,
    original: text,
    edited: text,
  }));
}

function lyricUnitCount(text: string) {
  return [...text.matchAll(/[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u{20000}-\u{2FA1F}\u3040-\u30FF]|[A-Za-z0-9]+/gu)].length;
}

type MidiOnsetGroup = { start: number; end: number };
type AllocatedMidiSection = MidiSection & { lyric_count: number; onset_count: number };

function groupMidiOnsets(notes: MidiNote[]): MidiOnsetGroup[] {
  const sorted = [...notes].sort((left, right) => left.start - right.start);
  const groups: MidiOnsetGroup[] = [];
  for (const note of sorted) {
    const current = groups[groups.length - 1];
    if (current && note.start - current.start <= 0.015) {
      current.end = Math.max(current.end, note.end);
    } else {
      groups.push({ start: note.start, end: note.end });
    }
  }
  return groups;
}

function restoreLrcDraft(projectId: string) {
  try {
    const raw = localStorage.getItem(`${MIDI_LRC_DRAFT_PREFIX}${projectId}`);
    if (!raw) return { source: '', lines: [] as MidiLyricLine[], enabled: false };
    const draft = JSON.parse(raw) as { source?: unknown; lines?: unknown; enabled?: unknown };
    const lines = Array.isArray(draft.lines) ? draft.lines.filter((line): line is MidiLyricLine => (
      typeof line === 'object' && line !== null
      && typeof (line as MidiLyricLine).id === 'string'
      && typeof (line as MidiLyricLine).original === 'string'
      && typeof (line as MidiLyricLine).edited === 'string'
      && ((line as MidiLyricLine).startOverride === undefined
        || (typeof (line as MidiLyricLine).startOverride === 'number' && Number.isFinite((line as MidiLyricLine).startOverride)))
    )) : [];
    return {
      source: typeof draft.source === 'string' ? draft.source : '',
      lines,
      enabled: Boolean(draft.enabled && lines.length),
    };
  } catch {
    localStorage.removeItem(`${MIDI_LRC_DRAFT_PREFIX}${projectId}`);
    return { source: '', lines: [] as MidiLyricLine[], enabled: false };
  }
}

function useMidiAudition(sections: MidiSection[]) {
  const [speed, setSpeed] = useState(1);
  const [playingSectionIndex, setPlayingSectionIndex] = useState<number | null>(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const sectionsRef = useRef(sections);
  const audioContextRef = useRef<AudioContext | null>(null);
  const activeOscillatorsRef = useRef(new Set<OscillatorNode>());
  const timeoutRef = useRef<number | null>(null);
  const intervalRef = useRef<number | null>(null);
  const runIdRef = useRef(0);

  useEffect(() => { sectionsRef.current = sections; }, [sections]);

  const clearScheduledAudio = useCallback(() => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
    timeoutRef.current = null;
    intervalRef.current = null;
    for (const oscillator of activeOscillatorsRef.current) {
      try { oscillator.stop(); } catch { /* The note may already have ended. */ }
    }
    activeOscillatorsRef.current.clear();
  }, []);

  const stop = useCallback(() => {
    runIdRef.current += 1;
    clearScheduledAudio();
    setPlayingSectionIndex(null);
    setPlaybackTime(0);
  }, [clearScheduledAudio]);

  useEffect(() => () => {
    runIdRef.current += 1;
    clearScheduledAudio();
    void audioContextRef.current?.close();
    audioContextRef.current = null;
  }, [clearScheduledAudio]);

  const play = useCallback(async (fromIndex?: number) => {
    stop();
    const runId = runIdRef.current;
    const list = sectionsRef.current;
    if (!list.length) return;

    try {
      if (!audioContextRef.current) {
        const AudioContextConstructor = window.AudioContext
          || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!AudioContextConstructor) throw new Error('此浏览器不支持旋律试听。');
        audioContextRef.current = new AudioContextConstructor();
      }
      const context = audioContextRef.current;
      await context.resume();
      if (runId !== runIdRef.current) return;

      const scheduleNotes = (section: MidiSection, startAt: number) => {
        for (const note of section.notes || []) {
          const oscillator = context.createOscillator();
          const envelope = context.createGain();
          const noteStart = startAt + Math.max(0, note.start) / speed;
          const noteDuration = Math.max(0.035, (note.end - note.start) / speed);
          const noteEnd = noteStart + noteDuration;
          const peak = 0.105 * Math.min(1, Math.max(0.15, note.velocity / 100));
          const attack = Math.min(0.008, noteDuration / 3);
          const release = Math.min(0.014, noteDuration / 3);
          oscillator.type = 'triangle';
          oscillator.frequency.setValueAtTime(440 * (2 ** ((note.pitch - 69) / 12)), noteStart);
          envelope.gain.setValueAtTime(0, noteStart);
          envelope.gain.linearRampToValueAtTime(peak, noteStart + attack);
          envelope.gain.setValueAtTime(peak, noteEnd - release);
          envelope.gain.linearRampToValueAtTime(0, noteEnd);
          oscillator.connect(envelope);
          envelope.connect(context.destination);
          oscillator.onended = () => {
            activeOscillatorsRef.current.delete(oscillator);
            oscillator.disconnect();
            envelope.disconnect();
          };
          activeOscillatorsRef.current.add(oscillator);
          oscillator.start(noteStart);
          oscillator.stop(noteEnd + 0.002);
        }
      };

      const firstIndex = Math.max(0, Math.min(fromIndex ?? 0, list.length - 1));
      const playSection = (index: number) => {
        if (runId !== runIdRef.current) return;
        const section = list[index];
        const duration = Math.max(0, section.end - section.start);
        const startAt = context.currentTime + 0.035;
        setPlayingSectionIndex(index);
        setPlaybackTime(0);
        scheduleNotes(section, startAt);
        intervalRef.current = window.setInterval(() => {
          setPlaybackTime(Math.min(duration, Math.max(0, context.currentTime - startAt) * speed));
        }, 35);
        timeoutRef.current = window.setTimeout(() => {
          if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
          intervalRef.current = null;
          if (fromIndex === undefined && index + 1 < list.length) {
            playSection(index + 1);
          } else {
            clearScheduledAudio();
            setPlayingSectionIndex(null);
            setPlaybackTime(0);
          }
        }, Math.max(60, duration / speed * 1000 + 45));
      };
      playSection(firstIndex);
    } catch (cause) {
      if (runId === runIdRef.current) {
        setPlayingSectionIndex(null);
        setPlaybackTime(0);
        window.alert(cause instanceof Error ? cause.message : '旋律试听启动失败。');
      }
    }
  }, [clearScheduledAudio, speed, stop]);

  return { speed, setSpeed, playingSectionIndex, playbackTime, play, stop };
}

function midiPitchName(pitch: number) {
  const names = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
  return `${names[((pitch % 12) + 12) % 12]}${Math.floor(pitch / 12) - 1}`;
}

function MidiNoteRoll({ section, progress, playing }: { section: MidiSection; progress: number; playing: boolean }) {
  const notes: MidiNote[] = section.notes || [];
  const duration = Math.max(0.1, section.end - section.start);
  const minPitch = Math.min(...notes.map((note) => note.pitch), 60) - 1;
  const maxPitch = Math.max(...notes.map((note) => note.pitch), 72) + 1;
  const pitchSpan = maxPitch - minPitch + 1;
  const plotTop = 7;
  const plotHeight = 84;
  const rowHeight = plotHeight / pitchSpan;
  const gridStep = duration <= 9 ? 0.5 : 1;
  const gridCount = Math.ceil(duration / gridStep);

  return (
    <div className="midi-note-roll-wrap" aria-label="MIDI 音符音高与节奏图">
      <svg className="midi-note-roll" viewBox="0 0 1000 112" preserveAspectRatio="none" role="img" aria-label={`本段 ${notes.length} 个音符的时间分布`}>
        <rect x="0" y="0" width="1000" height="112" rx="5" className="midi-roll-background" />
        {Array.from({ length: gridCount + 1 }, (_, index) => {
          const at = Math.min(duration, index * gridStep);
          const x = at / duration * 1000;
          const major = Math.abs(at - Math.round(at)) < 0.01;
          return <g key={`grid-${index}`}>
            <line x1={x} x2={x} y1="4" y2="95" className={major ? 'midi-roll-grid-major' : 'midi-roll-grid'} />
            {major && <text x={Math.min(992, x + 3)} y="106" className="midi-roll-time">{at.toFixed(0)}s</text>}
          </g>;
        })}
        {Array.from({ length: pitchSpan }, (_, index) => {
          const pitch = maxPitch - index;
          const y = plotTop + index * rowHeight;
          return pitch % 12 === 0
            ? <line key={`pitch-${pitch}`} x1="0" x2="1000" y1={y} y2={y} className="midi-roll-octave" />
            : null;
        })}
        {notes.map((note, index) => {
          const x = Math.max(0, note.start / duration * 1000);
          const width = Math.max(2, (note.end - note.start) / duration * 1000);
          const y = plotTop + (maxPitch - note.pitch) * rowHeight + rowHeight * 0.18;
          const height = Math.max(2.5, rowHeight * 0.64);
          return <rect key={`${note.start}-${note.pitch}-${index}`} x={x} y={y} width={Math.min(width, 1000 - x)} height={height} rx="2" className="midi-roll-note">
            <title>{midiPitchName(note.pitch)} · {note.start.toFixed(2)}–{note.end.toFixed(2)} 秒</title>
          </rect>;
        })}
        {playing && <line x1={progress / duration * 1000} x2={progress / duration * 1000} y1="2" y2="96" className="midi-roll-playhead" />}
      </svg>
      <div className="midi-roll-caption"><span>音符长短表示时值 · 悬停看音名</span><span>{notes.length} notes</span></div>
    </div>
  );
}

type Props = {
  singers: SingerProfile[];
  selectedSingerId: string;
  onSingerChange: (id: string) => void;
  onOpenSingerManager: () => void;
  refreshToken: number;
  initialProjectId: string | null;
};

export default function MidiStudio({ singers, selectedSingerId, onSingerChange, onOpenSingerManager, refreshToken, initialProjectId }: Props) {
  const [project, setProject] = useState<MidiProject | null>(null);
  const [midiFile, setMidiFile] = useState<File | null>(null);
  const [lrcSource, setLrcSource] = useState('');
  const [lrcLines, setLrcLines] = useState<MidiLyricLine[]>([]);
  const [lrcTimingDrafts, setLrcTimingDrafts] = useState<Record<string, string>>({});
  const [useLrcLyrics, setUseLrcLyrics] = useState(false);
  const [linePreviewUrls, setLinePreviewUrls] = useState<Record<number, string>>({});
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [steps, setSteps] = useState(32);
  const [pitchShift, setPitchShift] = useState(0);
  const [loading, setLoading] = useState(true);
  const [trackSaving, setTrackSaving] = useState(false);
  const [singerSaving, setSingerSaving] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const lrcFileInput = useRef<HTMLInputElement>(null);
  const eventSource = useRef<EventSource | null>(null);
  const absoluteNotes = useMemo(() => (project?.segments || []).flatMap((section) => (
    (section.notes || []).map((note) => ({
      start: section.start + note.start,
      end: section.start + note.end,
      pitch: note.pitch,
      velocity: note.velocity,
    }))
  )), [project?.segments]);
  const lrcMidiSections = useMemo(() => {
    const onsetGroups = groupMidiOnsets(absoluteNotes);
    const lyricCounts = lrcLines.map((line) => lyricUnitCount(line.edited));
    const totalLyrics = lyricCounts.reduce((sum, count) => sum + count, 0);
    const activeLineCount = lyricCounts.filter((count) => count > 0).length;
    const boundaries = Array(lrcLines.length + 1).fill(0) as number[];
    let cursor = 0;
    let cumulativeLyrics = 0;
    let activePosition = 0;
    for (let index = 0; index < lrcLines.length; index += 1) {
      boundaries[index] = cursor;
      const count = lyricCounts[index];
      if (count === 0 || totalLyrics === 0) {
        boundaries[index + 1] = cursor;
        continue;
      }
      cumulativeLyrics += count;
      activePosition += 1;
      const remainingLines = activeLineCount - activePosition;
      let next = activePosition === activeLineCount
        ? onsetGroups.length
        : Math.round(cumulativeLyrics / totalLyrics * onsetGroups.length);
      if (onsetGroups.length >= activeLineCount) {
        next = Math.max(cursor + 1, Math.min(next, onsetGroups.length - remainingLines));
      } else {
        next = Math.max(cursor, Math.min(next, onsetGroups.length));
      }
      boundaries[index + 1] = next;
      cursor = next;
    }

    const allocatedStarts = lrcLines.map((_, index) => (
      onsetGroups[boundaries[index]]?.start ?? (onsetGroups[boundaries[index] - 1]?.end ?? 0)
    ));
    const duration = project?.duration ?? 0;
    const starts: number[] = [];
    for (let index = 0; index < lrcLines.length; index += 1) {
      const requested = lrcLines[index].startOverride ?? allocatedStarts[index];
      const lower = index === 0 ? 0 : starts[index - 1] + 0.001;
      const upper = Math.max(lower, duration - (lrcLines.length - index - 1) * 0.001);
      starts.push(Math.max(lower, Math.min(requested, upper)));
    }

    return lrcLines.map((line, index): AllocatedMidiSection => {
    const start = starts[index];
    const end = starts[index + 1] ?? duration;
    const onsetCount = onsetGroups.filter((group) => group.start >= start && group.start < end).length;
    const notes = absoluteNotes.flatMap((note) => {
      const clippedStart = Math.max(start, note.start);
      const clippedEnd = Math.min(end, note.end);
      return clippedEnd > clippedStart ? [{ ...note, start: clippedStart - start, end: clippedEnd - start }] : [];
    });
    return {
      index,
      start,
      end,
      original: line.original,
      edited: line.edited,
      has_notes: notes.length > 0,
      note_count: notes.length,
      lyric_count: lyricCounts[index],
      onset_count: onsetCount,
      notes,
    };
    });
  }, [absoluteNotes, lrcLines, project?.duration]);
  const lrcMappingStats = useMemo(() => ({
    lyricUnits: lrcMidiSections.reduce((sum, section) => sum + section.lyric_count, 0),
    midiOnsets: lrcMidiSections.reduce((sum, section) => sum + section.onset_count, 0),
  }), [lrcMidiSections]);
  const auditionSections = useMemo(() => {
    if (!useLrcLyrics || !lrcMidiSections.length) return project?.segments || [];
    const introStart = lrcMidiSections[0]?.start || 0;
    const introNotes = absoluteNotes.flatMap((note) => {
      const end = Math.min(introStart, note.end);
      return end > note.start ? [{ ...note, start: note.start, end }] : [];
    });
    const intro: MidiSection[] = introStart > 0 ? [{
      index: -1,
      start: 0,
      end: introStart,
      original: '',
      edited: '',
      has_notes: introNotes.length > 0,
      note_count: introNotes.length,
      notes: introNotes,
    }] : [];
    return [...intro, ...lrcMidiSections];
  }, [absoluteNotes, lrcMidiSections, project?.segments, useLrcLyrics]);
  const audition = useMidiAudition(auditionSections);

  const refreshProject = useCallback(async (id: string) => {
    const latest = restoreDraft(await api.midiProject(id));
    setProject(latest);
    return latest;
  }, []);

  const connectJob = useCallback((jobId: string, projectId: string, previewLineIndex?: number) => {
    eventSource.current?.close();
    const events = watchJob(jobId, (job) => {
      setActiveJob(job);
      if (!['succeeded', 'failed', 'canceled'].includes(job.status)) return;
      events.close();
      eventSource.current = null;
      if (job.status === 'failed') setError(job.error || 'MIDI 歌声生成失败。');
      if (job.status === 'succeeded') {
        setError('');
        setToast(job.label || '整曲生成完成。');
        window.setTimeout(() => setToast(''), 4500);
        const lineIndex = previewLineIndex ?? job.line_index;
        if (job.kind === 'midi_preview' && lineIndex !== undefined && job.result?.asset_url) {
          setLinePreviewUrls((current) => ({ ...current, [lineIndex]: `${job.result!.asset_url}?v=${Date.now()}` }));
        }
      }
      void refreshProject(projectId).catch(() => undefined);
    });
    eventSource.current = events;
  }, [refreshProject]);

  useEffect(() => {
    let live = true;
    const savedId = initialProjectId || localStorage.getItem(LAST_MIDI_PROJECT);
    if (!savedId) {
      setLoading(false);
      return () => { live = false; };
    }
    localStorage.setItem(LAST_MIDI_PROJECT, savedId);
    api.midiProject(savedId).then((loaded) => {
      if (!live) return;
      const restored = restoreDraft(loaded);
      setProject(restored);
      const lrcDraft = loaded.lrc_draft || restoreLrcDraft(loaded.id);
      setLrcSource(lrcDraft.source);
      setLrcLines(lrcDraft.lines);
      setUseLrcLyrics(lrcDraft.enabled);
      setLinePreviewUrls({});
      const latest = loaded.jobs.find((job) => job.status === 'queued' || job.status === 'running') || loaded.jobs[0];
      if (latest) {
        setActiveJob(latest);
        if (latest.status === 'queued' || latest.status === 'running') connectJob(latest.id, loaded.id);
      }
    }).catch(() => {
      if (live) localStorage.removeItem(LAST_MIDI_PROJECT);
    }).finally(() => live && setLoading(false));
    return () => {
      live = false;
      eventSource.current?.close();
      eventSource.current = null;
    };
  }, [connectJob, initialProjectId]);

  useEffect(() => {
    if (!refreshToken || !project) return;
    void refreshProject(project.id).catch((cause) => setError(cause instanceof Error ? cause.message : '刷新 MIDI 工程失败。'));
  }, [refreshToken, project?.id, refreshProject]);

  useEffect(() => {
    if (!project) return;
    try {
      localStorage.setItem(
        `${MIDI_DRAFT_PREFIX}${project.id}`,
        JSON.stringify(project.segments.map((section) => section.edited)),
      );
    } catch {
      // Local lyrics remain editable when browser storage is unavailable.
    }
  }, [project?.id, project?.segments]);

  useEffect(() => {
    if (!project) return;
    try {
      localStorage.setItem(`${MIDI_LRC_DRAFT_PREFIX}${project.id}`, JSON.stringify({
        source: lrcSource,
        lines: lrcLines,
        enabled: useLrcLyrics,
      }));
    } catch {
      // The LRC editor stays available if browser storage is full.
    }
  }, [lrcLines, lrcSource, project?.id, useLrcLyrics]);

  useEffect(() => {
    if (!project || project.status !== 'ready') return;
    const timer = window.setTimeout(() => {
      void api.saveMidiDraft(
        project.id,
        project.segments.map((section) => section.edited),
        lrcSource,
        lrcLines,
        useLrcLyrics,
      ).catch((cause) => setError(cause instanceof Error ? cause.message : '保存 MIDI 草稿失败。'));
    }, 650);
    return () => window.clearTimeout(timer);
  }, [lrcLines, lrcSource, project?.id, project?.segments, project?.status, useLrcLyrics]);

  useEffect(() => {
    setLrcTimingDrafts({});
  }, [project?.id]);

  const newProject = () => {
    audition.stop();
    eventSource.current?.close();
    eventSource.current = null;
    if (project) {
      localStorage.removeItem(`${MIDI_DRAFT_PREFIX}${project.id}`);
      localStorage.removeItem(`${MIDI_LRC_DRAFT_PREFIX}${project.id}`);
    }
    localStorage.removeItem(LAST_MIDI_PROJECT);
    window.history.replaceState(null, '', '/');
    setProject(null);
    setMidiFile(null);
    setLrcSource('');
    setLrcLines([]);
    setUseLrcLyrics(false);
    setLinePreviewUrls({});
    setActiveJob(null);
    setError('');
    setToast('');
  };

  const upload = async () => {
    if (!midiFile) {
      setError('请先选择 MIDI 文件。');
      return;
    }
    setError('');
    setUploadProgress(0);
    try {
      const created = await uploadMidiProject(midiFile, selectedSingerId, setUploadProgress);
      localStorage.setItem(LAST_MIDI_PROJECT, created.id);
      window.history.replaceState(null, '', `/?project=${encodeURIComponent(created.id)}&type=midi`);
      setProject(created);
      setLrcSource('');
      setLrcLines([]);
      setUseLrcLyrics(false);
      setLinePreviewUrls({});
      setActiveJob(null);
      setUploadProgress(null);
      setMidiFile(null);
      if (fileInput.current) fileInput.current.value = '';
      setToast(`已读取 ${created.tracks.length} 条音轨，自动切成 ${created.segments.length} 段。`);
      window.setTimeout(() => setToast(''), 4500);
    } catch (cause) {
      setUploadProgress(null);
      setError(cause instanceof Error ? cause.message : 'MIDI 文件读取失败。');
    }
  };

  const changeTrack = async (trackIndex: number) => {
    if (!project || trackSaving || trackIndex === project.selected_track_index) return;
    audition.stop();
    setTrackSaving(true);
    setError('');
    try {
      const next = await api.selectMidiTrack(project.id, trackIndex);
      localStorage.removeItem(`${MIDI_DRAFT_PREFIX}${project.id}`);
      setProject(next);
      setLinePreviewUrls({});
      setToast('旋律音轨已切换；请重新对应各段歌词。');
      window.setTimeout(() => setToast(''), 4500);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '切换音轨失败。');
    } finally {
      setTrackSaving(false);
    }
  };

  const changeSinger = async (singerId: string) => {
    if (!project || singerSaving) return;
    setSingerSaving(true);
    setError('');
    try {
      const result = await api.setProjectSinger(project.id, singerId || null);
      onSingerChange(singerId);
      setProject((current) => current ? {
        ...current,
        singer_profile: result.singer_profile,
        last_generation: result.final_invalidated ? null : current.last_generation,
      } : current);
      setLinePreviewUrls({});
      setToast(singerId ? `所有片段将固定使用「${result.singer_profile?.name}」。` : '所有片段将使用模型默认参考音色。');
      window.setTimeout(() => setToast(''), 4500);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '更换歌手失败。');
    } finally {
      setSingerSaving(false);
    }
  };

  const updateLyrics = (index: number, text: string) => {
    setProject((current) => current ? {
      ...current,
      segments: current.segments.map((section, sectionIndex) => sectionIndex === index ? { ...section, edited: text } : section),
    } : current);
  };

  const importLrc = (source = lrcSource) => {
    if (!project) return;
    const parsed = parseMidiLrc(source);
    if (!parsed.length) {
      setError('没有找到有效的 LRC 歌词句，请检查时间戳和歌词格式。');
      return;
    }
    setError('');
    setLrcSource(source);
    setLrcLines(parsed);
    setUseLrcLyrics(true);
    setLinePreviewUrls({});
    setToast(`已载入 ${parsed.length} 句歌词；音符区间将按歌词字数从 MIDI 开头连续分配。`);
    window.setTimeout(() => setToast(''), 4500);
  };

  const loadLrcFile = async (file: File) => {
    try {
      importLrc(await file.text());
    } catch {
      setError('无法读取这个 LRC 文件，请尝试 UTF-8 编码或直接粘贴歌词。');
    }
  };

  const clearLrc = () => {
    setLrcSource('');
    setLrcLines([]);
    setUseLrcLyrics(false);
    setLinePreviewUrls({});
  };

  const updateLrcLine = (index: number, patch: Partial<MidiLyricLine>) => {
    setLrcLines((current) => current.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line));
    setLinePreviewUrls({});
  };

  const commitLrcStart = (index: number, value: string) => {
    if (!project) return;
    const line = lrcLines[index];
    if (!line) return;
    const clearDraft = () => setLrcTimingDrafts((current) => {
      const next = { ...current };
      delete next[line.id];
      return next;
    });
    const start = parseMidiTimestamp(value);
    if (start === null || start >= project.duration) {
      setError('时间格式应为 分:秒.百分秒，例如 01:23.45。');
      clearDraft();
      return;
    }
    const previous = lrcMidiSections[index - 1]?.start;
    const next = lrcMidiSections[index + 1]?.start ?? project.duration;
    if ((previous !== undefined && start - previous < 0.3) || next - start < 0.3) {
      setError('歌词时间必须保持递增，相邻两句至少间隔 0.30 秒。');
      clearDraft();
      return;
    }
    clearDraft();
    setError('');
    if (Math.abs(start - lrcMidiSections[index].start) >= 0.005) {
      updateLrcLine(index, { startOverride: start });
    }
  };

  const previewLrcLine = async (index: number) => {
    if (!project || isBusy || isPreviewBusy) return;
    const line = lrcLines[index];
    const section = lrcMidiSections[index];
    if (!line || !section || !line.edited.trim()) {
      setError(`请先填写第 ${index + 1} 句歌词。`);
      return;
    }
    if (!section.has_notes) {
      setError(`第 ${index + 1} 句没有分到 MIDI 音符；请检查歌词字数，或把相邻短句合并。`);
      return;
    }
    if (section.lyric_count === 0 || section.onset_count === 0 || section.end - section.start < 0.3 || section.end - section.start > 44.5) {
      setError(`第 ${index + 1} 句的歌词字数或分配到的 MIDI 音符不足以试听；请补充歌词字数或合并相邻短句。`);
      return;
    }
    setError('');
    audition.stop();
    setLinePreviewUrls((current) => {
      const next = { ...current };
      delete next[index];
      return next;
    });
    try {
      const result = await api.previewMidiLine(project.id, {
        index,
        start: section.start,
        end: section.end,
        lyrics: line.edited,
      }, steps, pitchShift);
      const queued: Job = {
        id: result.job_id,
        project_id: project.id,
        parent_id: null,
        line_index: index,
        kind: 'midi_preview',
        status: 'queued',
        progress: 0,
        label: `等待第 ${index + 1} 句重唱试听`,
        error: null,
        result: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setActiveJob(queued);
      connectJob(result.job_id, project.id, index);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '单句重唱试听提交失败。');
    }
  };

  const generate = async () => {
    if (!project) return;
    audition.stop();
    if (useLrcLyrics) {
      const missing = lrcLines.findIndex((line) => !line.edited.trim());
      if (missing >= 0) {
        setError(`请先填写第 ${missing + 1} 句歌词。`);
        document.getElementById(`midi-lrc-line-${missing}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      const invalid = lrcMidiSections.findIndex((section) => (
        section.lyric_count === 0 || section.onset_count === 0 || !section.has_notes
        || section.end - section.start < 0.3 || section.end - section.start > 44.5
      ));
      if (invalid >= 0) {
        setError(`第 ${invalid + 1} 句没有获得有效的连续 MIDI 音符区间；请检查该句歌词字数，或合并/拆分相邻句子。`);
        document.getElementById(`midi-lrc-line-${invalid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      setError('');
      try {
        const result = await api.generateMidiLines(
          project.id,
          lrcLines.map((line, index): MidiLyricLinePayload => ({
            index,
            start: lrcMidiSections[index].start,
            end: lrcMidiSections[index].end,
            lyrics: line.edited,
          })),
          steps,
          pitchShift,
        );
        const queued: Job = {
          id: result.job_id,
          project_id: project.id,
          parent_id: null,
          kind: 'midi_generate_song',
          status: 'queued',
          progress: 0,
          label: `等待 LRC 逐句生成 · ${lrcLines.length} 句`,
          error: null,
          result: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        setActiveJob(queued);
        setProject((current) => current ? { ...current, last_generation: null } : current);
        connectJob(result.job_id, project.id);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'LRC 逐句生成任务提交失败。');
      }
      return;
    }
    const missing = project.segments.findIndex((section) => section.has_notes && !section.edited.trim());
    if (missing >= 0) {
      setError(`请先填写第 ${missing + 1} 段歌词。`);
      document.getElementById(`midi-section-${missing}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    setError('');
    try {
      const result = await api.generateMidi(
        project.id,
        project.segments.map((section) => section.edited),
        steps,
        pitchShift,
      );
      const queued: Job = {
        id: result.job_id,
        project_id: project.id,
        parent_id: null,
        kind: 'midi_generate_song',
        status: 'queued',
        progress: 0,
        label: `等待 MIDI 歌声生成 · ${project.segments.filter((section) => section.has_notes).length} 段`,
        error: null,
        result: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setActiveJob(queued);
      setProject((current) => current ? { ...current, last_generation: null } : current);
      connectJob(result.job_id, project.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '生成任务提交失败。');
    }
  };

  const cancel = async () => {
    if (!activeJob) return;
    try {
      const updated = await api.cancel(activeJob.id);
      setActiveJob(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '停止任务失败。');
    }
  };

  const isBusy = activeJob?.kind === 'midi_generate_song' && (activeJob.status === 'queued' || activeJob.status === 'running');
  const isPreviewBusy = activeJob?.kind === 'midi_preview' && (activeJob.status === 'queued' || activeJob.status === 'running');
  const isAnyInferenceBusy = isBusy || isPreviewBusy;
  const melodicTracks = project?.tracks.filter((track) => !track.is_drum && track.note_count > 0) || [];
  const filledSections = useLrcLyrics
    ? lrcLines.filter((line) => line.edited.trim()).length
    : project?.segments.filter((section) => section.has_notes && section.edited.trim()).length || 0;

  if (loading) return <div className="midi-loading"><LoaderCircle className="spin" size={22} /><span>正在读取 MIDI 工程…</span></div>;

  return (
    <>
      <section className="page-hero midi-hero">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" />MELODY IN, LYRICS ON <span className="eyebrow-mono">/ MIDI 填词</span></div>
          <h1>把旋律交给 MIDI，<em>把声音留给歌手。</em></h1>
          <p>上传旋律 MIDI，按自动切分的乐句填词；所有片段复用同一歌手，最后拼成整曲。</p>
        </div>
        <div className="hero-art" aria-hidden="true"><div className="hero-ring ring-one" /><div className="hero-ring ring-two" /><div className="hero-core"><FileMusic size={25} /></div><span className="hero-orbit orbit-a" /><span className="hero-orbit orbit-b" /><span className="hero-orbit orbit-c" /></div>
      </section>

      {error && <div className="alert alert-error"><X size={17} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误"><X size={15} /></button></div>}
      {toast && <div className="toast"><Check size={16} />{toast}<button onClick={() => setToast('')} aria-label="关闭提示"><X size={14} /></button></div>}

      {!project ? (
        <section className="upload-grid midi-upload-grid">
          <div className="panel upload-panel midi-upload-panel">
            <div className="panel-heading"><div><div className="step-chip">01 <span>MIDI INPUT</span></div><h2>新建 MIDI 填词工程</h2><p>上传含旋律音符的 Standard MIDI 文件，模型按音符合成演唱。</p></div><span className="panel-index">01 / 03</span></div>
            <div className="singer-select-row"><div className="singer-select-copy"><span className="singer-select-icon"><Mic2 size={16} /></span><div><b>统一歌手</b><small>{selectedSingerId ? '同一份参考音频会用于每个 MIDI 片段' : '当前使用模型默认参考音色，也可管理自己的歌手'}</small></div></div><select aria-label="MIDI 工程歌手" value={selectedSingerId} onChange={(event) => onSingerChange(event.target.value)}><option value="">模型默认参考音色</option>{singers.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select><button className="button button-quiet" onClick={onOpenSingerManager}><Mic2 size={13} />管理</button></div>
            <div className={`dropzone midi-dropzone ${midiFile ? 'dropzone-loaded' : ''}`} onDragOver={(event) => { event.preventDefault(); event.currentTarget.classList.add('dropzone-active'); }} onDragLeave={(event) => event.currentTarget.classList.remove('dropzone-active')} onDrop={(event) => { event.preventDefault(); event.currentTarget.classList.remove('dropzone-active'); const file = event.dataTransfer.files[0]; if (file) setMidiFile(file); }} onClick={() => fileInput.current?.click()}>
              <input ref={fileInput} type="file" accept=".mid,.midi,audio/midi,audio/x-midi" hidden onChange={(event) => setMidiFile(event.target.files?.[0] || null)} />
              <div className="drop-icon">{midiFile ? <FileMusic size={22} /> : <Upload size={22} />}</div>
              <div className="drop-copy"><b>{midiFile ? midiFile.name : '把 MIDI 拖到这里，或点击选择'}</b><span>{midiFile ? `${(midiFile.size / (1024 * 1024)).toFixed(2)} MB · 已就绪` : '支持 Standard MIDI：.mid / .midi · 最大 100 MB'}</span></div>
              {midiFile ? <button className="file-clear" onClick={(event) => { event.stopPropagation(); setMidiFile(null); }} aria-label="移除 MIDI"><X size={15} /></button> : <span className="drop-shortcut">BROWSE</span>}
            </div>
            <div className="upload-footer"><div className="privacy-note"><Radio size={14} />MIDI 与歌声都只在本机处理</div><button className="button button-primary" disabled={uploadProgress !== null} onClick={() => void upload()}>{uploadProgress !== null ? <><LoaderCircle className="spin" size={16} />读取中 {uploadProgress}%</> : <>读取 MIDI 并填词<ChevronRight size={16} /></>}</button></div>
            {uploadProgress !== null && <div className="upload-progress"><span style={{ width: `${uploadProgress}%` }} /></div>}
          </div>
          <div className="panel explainer-panel midi-explainer">
            <div className="step-chip">FLOW <span>3 STEPS</span></div>
            <h3>旋律、歌词、歌手<br /><em>逐段组合成歌。</em></h3>
            <div className="flow-list"><div className="flow-step"><span className="flow-number">01</span><span className="flow-connector" /><div><b>读取音符</b><small>自动选择主旋律，可手动切换音轨</small></div><Music2 size={17} /></div><div className="flow-step"><span className="flow-number">02</span><span className="flow-connector" /><div><b>按段填词</b><small>自动按节拍切分，歌词由你校对</small></div><FileMusic size={17} /></div><div className="flow-step"><span className="flow-number">03</span><div><b>同声线生成</b><small>复用一份歌手参考并拼成 WAV</small></div><Sparkles size={17} /></div></div>
            <div className="explainer-foot"><span className="tiny-wave">▂ ▄ ▆ ▃ ▇ ▄ ▂ ▅ ▃ ▇ ▄ ▂ ▆ ▃ ▅</span><span>YINGMUSIC · MIDI INFERENCE</span></div>
          </div>
        </section>
      ) : (
        <>
          <section className="panel midi-source-strip">
            <div className="source-cover"><div className="cover-orbit" /><FileMusic size={21} /></div>
            <div className="midi-source-title"><span className="micro-label">MIDI TRACK <span className="source-live"><i /> READY</span></span><b>{project.midi_filename}</b><small>{clock(project.duration)} · {project.segments.filter((section) => section.has_notes).length} 个有音符片段 · {melodicTracks.length} 条旋律音轨</small></div>
            <label className="midi-control-label">主旋律音轨<select value={project.selected_track_index} onChange={(event) => void changeTrack(Number(event.target.value))} disabled={trackSaving || isAnyInferenceBusy}>{melodicTracks.map((track) => <option key={track.index} value={track.index}>{track.name} · {track.note_count} 音符</option>)}</select></label>
            <label className="midi-control-label midi-singer-control">统一歌手<select aria-label="当前 MIDI 工程统一歌手" value={project.singer_profile?.id || ''} onChange={(event) => void changeSinger(event.target.value)} disabled={singerSaving || isAnyInferenceBusy}><option value="">模型默认参考音色</option>{project.singer_profile && !singers.some((profile) => profile.id === project.singer_profile?.id) && <option value={project.singer_profile.id}>{project.singer_profile.name}（资料已移除）</option>}{singers.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
            <button className="button button-quiet" onClick={newProject}><Plus size={15} />新建</button>
          </section>

          {isAnyInferenceBusy && <section className="panel midi-job-banner"><div className="waiting-icon"><LoaderCircle className="spin" size={20} /></div><div><b>{activeJob?.label || 'MIDI 歌声生成中'}</b><p>{isPreviewBusy ? '逐句试听使用当前统一歌手；GPU 单路运行。' : 'GPU 单路顺序处理，期间所有句子都会沿用当前歌手。'}</p></div><div className="midi-job-progress"><b>{Math.round((activeJob?.progress || 0) * 100)}%</b><span><i style={{ width: `${Math.max(3, (activeJob?.progress || 0) * 100)}%` }} /></span></div><button className="button button-danger" onClick={() => void cancel()}><Square size={13} fill="currentColor" />停止</button></section>}

          <section className="midi-editor-layout">
            <div className="midi-editor-main">
              <div className="panel midi-lyrics-panel">
                <div className="midi-panel-heading"><div><div className="step-chip">02 <span>LYRIC MAPPING</span></div><h2>{useLrcLyrics ? '按 LRC 一句一句填词' : '按旋律段落填写歌词'}</h2><p>{useLrcLyrics ? 'LRC 时间戳忽略；初始句子起点按歌词字数映射 MIDI 音符。可手动修订起点，上一句结束点会同步跟随。' : '每个文本框对应一段 MIDI 旋律；模型会在段内自动把歌词映射到音符。长曲按节拍切成不超过 38 秒的片段。'}</p></div><div className="midi-section-count"><b>{(useLrcLyrics ? lrcLines.length : project.segments.length).toString().padStart(2, '0')}</b><span>{useLrcLyrics ? '歌词句' : '乐句段'}</span></div></div>
                <div className="midi-lrc-import">
                  <div className="midi-lrc-import-head"><div><b>用 LRC 参考分句</b><small>按歌词字数映射音符；可逐句修订起点，前一句结束点自动同步。</small></div><input ref={lrcFileInput} type="file" accept=".lrc,.txt,text/plain" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void loadLrcFile(file); event.target.value = ''; }} /><button className="button button-quiet" onClick={() => lrcFileInput.current?.click()} disabled={isAnyInferenceBusy}><Upload size={13} />导入 LRC</button></div>
                  <textarea className="midi-lrc-source" value={lrcSource} onChange={(event) => setLrcSource(event.target.value)} rows={3} placeholder={'也可以直接粘贴带时间戳的 LRC，例如：\n[00:12.30]第一句歌词\n[00:16.80]第二句歌词'} disabled={isAnyInferenceBusy} aria-label="LRC 歌词内容" />
                  <div className="midi-lrc-import-footer"><span>{lrcLines.length ? `已载入 ${lrcLines.length} 句 · ${lrcMappingStats.lyricUnits} 字/词 → ${lrcMappingStats.midiOnsets} 个 MIDI 起点${lrcMappingStats.lyricUnits !== lrcMappingStats.midiOnsets ? `（相差 ${Math.abs(lrcMappingStats.lyricUnits - lrcMappingStats.midiOnsets)}）` : ''} · 改字会重新分配整条音符序列` : '支持标准 LRC；时间标签仅帮助识别歌词行'}</span><div><button className="button button-quiet" onClick={() => importLrc()} disabled={isAnyInferenceBusy || !lrcSource.trim()}><FileMusic size={13} />解析并按句填词</button>{lrcLines.length > 0 && <><div className="midi-mode-toggle"><button className={useLrcLyrics ? 'selected' : ''} onClick={() => setUseLrcLyrics(true)}>LRC 逐句</button><button className={!useLrcLyrics ? 'selected' : ''} onClick={() => setUseLrcLyrics(false)}>MIDI 分段</button></div><button className="text-button midi-clear-lrc" onClick={clearLrc} disabled={isAnyInferenceBusy}>清空 LRC</button></>}</div></div>
                </div>
                {melodicTracks.length > 1 && <div className="midi-track-note"><Music2 size={14} />MIDI 含多条旋律音轨；当前选中「{project.tracks.find((track) => track.index === project.selected_track_index)?.name}」。鼓组不会送入歌声模型。</div>}
                <div className="midi-audition-toolbar">
                  <div className="midi-audition-copy"><span className="midi-audition-icon"><AudioLines size={16} /></span><div><b>先听旋律节奏，再填歌词</b><small>试听音包含 MIDI 音高和时值；谱图悬停可看 C4、D♯4 等音名。歌声试听由 GPU 生成。</small></div></div>
                  <div className="midi-audition-actions">
                    <button className="button button-audition" onClick={() => void audition.play()} disabled={isAnyInferenceBusy}><Play size={13} fill="currentColor" />试听整条旋律</button>
                    {audition.playingSectionIndex !== null && <button className="button button-stop-audition" onClick={audition.stop}><Square size={12} fill="currentColor" />停止</button>}
                    <label className="midi-speed-control"><span>速度</span><input aria-label="MIDI 试听速度" type="range" min="0.5" max="1.5" step="0.1" value={audition.speed} onChange={(event) => audition.setSpeed(Number(event.target.value))} disabled={audition.playingSectionIndex !== null} /><b>{audition.speed.toFixed(1)}×</b></label>
                  </div>
                </div>
                {useLrcLyrics && lrcLines.length > 0 ? <div className="midi-lrc-lines">
                  {lrcLines.map((line, index) => {
                    const section = lrcMidiSections[index];
                    const playingIndex = index + ((lrcMidiSections[0]?.start || 0) > 0 ? 1 : 0);
                    const phrasePlaying = audition.playingSectionIndex === playingIndex;
                    const previewRunning = isPreviewBusy && activeJob?.line_index === index;
                    return <article className="midi-lrc-line-card" id={`midi-lrc-line-${index}`} key={line.id}>
                      <div className="midi-lrc-line-heading"><span className="midi-section-number">{(index + 1).toString().padStart(2, '0')}</span><label className="midi-lrc-time-edit"><input type="text" value={lrcTimingDrafts[line.id] ?? formatMidiTimestamp(section.start)} onChange={(event) => setLrcTimingDrafts((current) => ({ ...current, [line.id]: event.target.value }))} onBlur={(event) => commitLrcStart(index, event.currentTarget.value)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }} onClick={(event) => event.stopPropagation()} disabled={isAnyInferenceBusy} title="编辑此句开始时间；结束时间自动与下一句衔接" aria-label={`第 ${index + 1} 句开始时间，格式分:秒.百分秒`} />{line.startOverride !== undefined && <button type="button" className="midi-lrc-time-reset" onClick={() => updateLrcLine(index, { startOverride: undefined })} disabled={isAnyInferenceBusy} aria-label={`第 ${index + 1} 句恢复自动起点`}>恢复自动</button>}<span>→ {formatMidiTimestamp(section.end)}</span></label><small>歌词 {section.lyric_count} 字/词 · 当前区间 {section.onset_count} 个音符起点 / {section.note_count} 个 MIDI 音符</small></div>
                      <textarea className="midi-lrc-line-text" value={line.edited} onChange={(event) => updateLrcLine(index, { edited: event.target.value })} maxLength={500} rows={2} placeholder={`第 ${index + 1} 句歌词`} aria-label={`LRC 第 ${index + 1} 句歌词`} disabled={isAnyInferenceBusy} />
                      <div className="midi-lrc-line-actions"><button className="button button-phrase-audition" onClick={() => phrasePlaying ? audition.stop() : void audition.play(playingIndex)} disabled={isAnyInferenceBusy || !section.has_notes}>{phrasePlaying ? <><Square size={11} fill="currentColor" />停止旋律</> : <><Play size={11} fill="currentColor" />试听 MIDI</>}</button><button className="button button-line-render" onClick={() => void previewLrcLine(index)} disabled={isAnyInferenceBusy || !line.edited.trim() || !section.has_notes}>{previewRunning ? <><LoaderCircle className="spin" size={12} />生成中</> : <><WandSparkles size={12} />生成重唱试听</>}</button>{linePreviewUrls[index] && <audio className="midi-line-audio" controls preload="metadata" src={linePreviewUrls[index]} aria-label={`第 ${index + 1} 句重唱试听`} />}</div>
                      <MidiNoteRoll section={section} progress={phrasePlaying ? audition.playbackTime : 0} playing={phrasePlaying} />
                    </article>;
                  })}
                </div> : <div className="midi-section-list">
                  {project.segments.map((section, index) => (
                    <article className={`midi-section-card ${section.has_notes ? '' : 'midi-rest-card'}`} id={`midi-section-${index}`} key={`${section.start}-${index}`}>
                      <div className="midi-section-meta"><span className="midi-section-number">{(index + 1).toString().padStart(2, '0')}</span><b>{clock(section.start)} — {clock(section.end)}</b><small>{section.has_notes ? `${section.note_count} 个音符` : '休止区间'}</small>{section.has_notes && <button className="button button-phrase-audition" onClick={() => audition.playingSectionIndex === index ? audition.stop() : void audition.play(index)} disabled={isAnyInferenceBusy}>{audition.playingSectionIndex === index ? <><Square size={11} fill="currentColor" />停止</> : <><Play size={11} fill="currentColor" />试听本段</>}</button>}</div>
                      {section.has_notes ? <><textarea value={section.edited} onChange={(event) => updateLyrics(index, event.target.value)} maxLength={500} rows={3} placeholder={`第 ${index + 1} 段歌词。可以输入一句或一小段词。`} aria-label={`MIDI 第 ${index + 1} 段歌词`} disabled={isAnyInferenceBusy} /><MidiNoteRoll section={section} progress={audition.playingSectionIndex === index ? audition.playbackTime : 0} playing={audition.playingSectionIndex === index} /></> : <div className="midi-rest-note">该区间所选音轨没有音符，拼接时会保留等长静音。</div>}
                    </article>
                  ))}
                </div>}
              </div>

              <section className="panel midi-render-panel">
                <div className="midi-render-copy"><div className="step-chip">03 <span>GENERATE & JOIN</span></div><h2>用同一歌手生成整曲</h2><p>每段都会使用工程中的同一份歌手参考音频和参考歌词，随后按 MIDI 的时间顺序拼接。</p></div>
                <div className="midi-render-controls">
                  <label className="midi-slider-label" htmlFor="midi-step-range"><span>扩散步数</span><b>{steps}<small> / 64</small></b></label>
                  <input id="midi-step-range" type="range" min="8" max="64" step="4" value={steps} onChange={(event) => setSteps(Number(event.target.value))} disabled={isAnyInferenceBusy} />
                  <label className="midi-slider-label midi-pitch-label" htmlFor="midi-pitch-range"><span>整体移调</span><b>{pitchShift > 0 ? `+${pitchShift}` : pitchShift} <small>半音</small></b></label>
                  <input id="midi-pitch-range" type="range" min="-12" max="12" step="1" value={pitchShift} onChange={(event) => setPitchShift(Number(event.target.value))} disabled={isAnyInferenceBusy} />
                </div>
                <div className="midi-generate-action"><span>已填写 {filledSections}/{useLrcLyrics ? lrcLines.length : project.segments.filter((section) => section.has_notes).length} 句</span>{isAnyInferenceBusy ? <button className="button button-danger" onClick={() => void cancel()}><Square size={14} fill="currentColor" />停止任务</button> : <button className="button button-primary" onClick={() => void generate()} disabled={useLrcLyrics && !lrcLines.length}><WandSparkles size={16} />开始生成并拼接</button>}</div>
              </section>

              {activeJob && !isBusy && activeJob.kind === 'midi_generate_song' && activeJob.status === 'failed' && <div className="alert alert-error"><X size={16} /><span>{activeJob.error || activeJob.label}</span></div>}
              {project.last_generation && <section className="panel midi-final-panel"><div className="final-icon"><Check size={18} /></div><div className="final-title"><span className="micro-label">MIDI MASTER · {project.last_generation.steps} STEPS</span><b>整曲已完成</b><small>{project.last_generation.changed_lines} 个有音符片段 · WAV / 44.1 kHz</small></div><audio className="midi-audio-player" controls preload="metadata" src={`/api/projects/${project.id}/assets/final?v=${project.last_generation.created_at}`} /><a className="button button-download" href={`/api/projects/${project.id}/assets/final`} download><Download size={15} />下载 WAV</a></section>}
            </div>

            <aside className="midi-side-column">
              <div className="panel midi-voice-card"><div className="step-chip"><Mic2 size={12} /> SAME SINGER</div><h3>声线始终一致</h3><p>当前工程的所有 MIDI 片段共用这份参考音频与参考歌词。切换歌手后，旧成品会失效。</p><div className="midi-voice-name"><span><Mic2 size={15} /></span><b>{project.singer_profile?.name || '模型默认参考音色'}</b></div><button className="button button-quiet" onClick={onOpenSingerManager}>管理歌手资料</button></div>
              <div className="panel midi-tip-card"><Sparkles size={15} /><div><b>填词提示</b><span>{useLrcLyrics ? 'LRC 只决定歌词顺序和分句；时间戳忽略。每句按有效字/词数量占比分配连续 MIDI 音符，先试听旋律和重唱。' : '每段可以放一两句。过长时请拆分文本段落；模型会自行对齐音符，不需要逐音符输入拼音。'}</span></div></div>
            </aside>
          </section>
        </>
      )}
      <footer className="page-footer"><span>SONG OF THESEUS <i>·</i> MIDI LYRIC MODE</span><span>PROCESSING RUNS LOCALLY <span className="footer-pulse" /></span></footer>
    </>
  );
}
