export type Segment = {
  start: number;
  end: number;
  original: string;
  edited: string;
};

export type Job = {
  id: string;
  project_id: string;
  parent_id: string | null;
  kind: string;
  line_index?: number;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | string;
  progress: number;
  label: string;
  error: string | null;
  result: { asset_url?: string; line_index?: number } | null;
  line_count?: number;
  lines_done?: number;
  cancel_requested?: boolean;
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  status: 'preparing' | 'ready' | 'failed' | string;
  lyrics_source?: string;
  singer_profile?: { id: string; name: string; updated_at?: string } | null;
  duration: number;
  segments: Segment[];
  created_at: string;
  last_generation: { steps: number; changed_lines: number; created_at: string } | null;
  latest_previews: Record<string, { lyrics: string; created_at: string }>;
  jobs: Job[];
};

export type SingerProfile = {
  id: string;
  name: string;
  reference_text: string;
  duration: number;
  created_at: string;
  updated_at: string;
  audio_url: string;
};

export type MidiTrack = {
  index: number;
  name: string;
  program: number;
  is_drum: boolean;
  note_count: number;
  duration: number;
};

export type MidiNote = {
  start: number;
  end: number;
  pitch: number;
  velocity: number;
};

export type MidiLyricLine = {
  id: string;
  original: string;
  edited: string;
  /** User-corrected phrase start in seconds; absent means use the lyric-to-MIDI allocation. */
  startOverride?: number;
};

export type MidiLyricLinePayload = {
  index: number;
  start: number;
  end: number;
  lyrics: string;
};

export type MidiSection = {
  index: number;
  start: number;
  end: number;
  original: string;
  edited: string;
  has_notes: boolean;
  note_count: number;
  notes: MidiNote[];
};

export type MidiProject = {
  id: string;
  status: string;
  workflow_type: 'midi';
  duration: number;
  midi_filename: string;
  tracks: MidiTrack[];
  selected_track_index: number;
  segments: MidiSection[];
  singer_profile: Project['singer_profile'];
  lrc_draft?: { source: string; lines: MidiLyricLine[]; enabled: boolean } | null;
  created_at: string;
  last_generation: Project['last_generation'];
  jobs: Job[];
};

export type Health = {
  online: boolean;
  device: string;
  gpu_name: string;
  gpu_free_gib: number | null;
  gpu_total_gib: number | null;
  queued_tasks: number;
  running_tasks: number;
  scheduler_workers: number;
  transcription: boolean;
};

export type WorkspaceSummary = {
  id: string;
  workflow_type: 'rewrite' | 'midi';
  title: string;
  status: string;
  duration: number;
  line_count: number;
  created_at: string;
  updated_at: string;
  singer_name: string | null;
  last_generation: { steps: number; changed_lines: number } | null;
};
