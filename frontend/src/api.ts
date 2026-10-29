import type { Health, Job, MidiLyricLine, MidiLyricLinePayload, MidiProject, Project, SingerProfile, WorkspaceSummary } from './types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : message;
    } catch {
      // Use the HTTP status when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>('/api/health'),
  workspaces: () => request<WorkspaceSummary[]>('/api/workspaces'),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  midiProject: (id: string) => request<MidiProject>(`/api/midi-projects/${id}`),
  queue: () => request<Job[]>('/api/queue'),
  singers: () => request<SingerProfile[]>('/api/singers'),
  createSinger: (name: string, referenceText: string, audio: File) => {
    const form = new FormData();
    form.append('name', name);
    form.append('reference_text', referenceText);
    form.append('audio', audio);
    return request<SingerProfile>('/api/singers', { method: 'POST', body: form });
  },
  updateSinger: (id: string, name: string, referenceText: string, audio: File | null) => {
    const form = new FormData();
    form.append('name', name);
    form.append('reference_text', referenceText);
    if (audio) form.append('audio', audio);
    return request<SingerProfile>(`/api/singers/${id}`, { method: 'PUT', body: form });
  },
  deleteSinger: (id: string) => request<{ id: string; status: string }>(`/api/singers/${id}`, { method: 'DELETE' }),
  setProjectSinger: (id: string, singerId: string | null) =>
    request<{ singer_profile: Project['singer_profile']; invalidated_previews: boolean; final_invalidated: boolean }>(`/api/projects/${id}/singer`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ singer_id: singerId }),
    }),
  saveLyricsDraft: (id: string, lyrics: string[]) =>
    request<{ status: string }>(`/api/projects/${id}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lyrics }),
    }),
  saveMidiDraft: (id: string, edited: string[], lrcSource: string, lrcLines: MidiLyricLine[], useLrcLyrics: boolean) =>
    request<{ status: string }>(`/api/midi-projects/${id}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        edited,
        lrc_source: lrcSource,
        lrc_lines: lrcLines,
        use_lrc_lyrics: useLrcLyrics,
      }),
    }),
  selectMidiTrack: (id: string, trackIndex: number) =>
    request<MidiProject>(`/api/midi-projects/${id}/track`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_index: trackIndex }),
    }),
  generateMidi: (id: string, lyrics: string[], steps: number, pitchShift: number) =>
    request<{ job_id: string; status: string }>(`/api/midi-projects/${id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lyrics, steps, pitch_shift: pitchShift }),
    }),
  generateMidiLines: (id: string, lines: MidiLyricLinePayload[], steps: number, pitchShift: number) =>
    request<{ job_id: string; status: string }>(`/api/midi-projects/${id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, steps, pitch_shift: pitchShift }),
    }),
  previewMidiLine: (id: string, line: MidiLyricLinePayload, steps: number, pitchShift: number) =>
    request<{ job_id: string; status: string }>(`/api/midi-projects/${id}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...line, steps, pitch_shift: pitchShift }),
    }),
  updateTimings: (id: string, starts: number[], ends: number[]) =>
    request<{ starts: number[]; ends: number[]; invalidated_preview_indices: number[]; final_invalidated: boolean }>(`/api/projects/${id}/timings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ starts, ends }),
    }),
  preview: (id: string, index: number, lyrics: string, steps: number) =>
    request<{ job_id: string; status: string }>(`/api/projects/${id}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index, lyrics, steps }),
    }),
  generate: (id: string, lines: { index: number; text: string }[], steps: number) =>
    request<{ job_id: string; status: string; changed_lines: number }>(`/api/projects/${id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, steps }),
    }),
  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
};

export function uploadMidiProject(
  midi: File,
  singerId: string,
  onProgress: (percent: number) => void,
): Promise<MidiProject> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('midi_file', midi);
    form.append('singer_id', singerId);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/midi-projects');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => reject(new Error('无法连接本地服务。'));
    xhr.onload = () => {
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(xhr.responseText) as Record<string, unknown>;
      } catch {
        reject(new Error(`服务器返回异常（${xhr.status}）`));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as MidiProject);
      } else {
        reject(new Error(typeof body.detail === 'string' ? body.detail : `MIDI 上传失败（${xhr.status}）`));
      }
    };
    xhr.send(form);
  });
}

export function uploadProject(
  audio: File,
  lrc: File | null,
  pastedLrc: string,
  autoTranscribe: boolean,
  singerId: string,
  onProgress: (percent: number) => void,
): Promise<{ project_id: string; job_id: string; status: string }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('audio', audio);
    if (lrc) form.append('lrc_file', lrc);
    if (pastedLrc.trim()) form.append('pasted_lrc', pastedLrc);
    form.append('auto_transcribe', String(autoTranscribe));
    form.append('singer_id', singerId);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/projects');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => reject(new Error('无法连接本地服务。'));
    xhr.onload = () => {
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(xhr.responseText) as Record<string, unknown>;
      } catch {
        reject(new Error(`服务器返回异常（${xhr.status}）`));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as { project_id: string; job_id: string; status: string });
      } else {
        reject(new Error(typeof body.detail === 'string' ? body.detail : `上传失败（${xhr.status}）`));
      }
    };
    xhr.send(form);
  });
}

export function watchJob(id: string, onUpdate: (job: Job) => void): EventSource {
  const events = new EventSource(`/api/jobs/${id}/events`);
  events.onmessage = (event) => {
    try {
      const job = JSON.parse(event.data) as Job;
      onUpdate(job);
      if (['succeeded', 'failed', 'canceled'].includes(job.status)) events.close();
    } catch {
      // Ignore malformed event payloads; EventSource reconnects automatically.
    }
  };
  return events;
}
