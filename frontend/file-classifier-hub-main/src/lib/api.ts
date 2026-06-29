// API client for FastAPI File Classifier backend.

const DEFAULT_BACKEND = "http://localhost:8000";

export function getBackendUrl(): string {
  if (typeof window === "undefined") return DEFAULT_BACKEND;
  const stored = localStorage.getItem("fc_backend_url");
  if (stored) {
    // Normalize 127.0.0.1 → localhost so OAuth cookies stay on one domain
    const normalized = stored.replace("://127.0.0.1", "://localhost");
    if (normalized !== stored) localStorage.setItem("fc_backend_url", normalized);
    return normalized;
  }
  return DEFAULT_BACKEND;
}

export function setBackendUrl(url: string) {
  localStorage.setItem("fc_backend_url", url.replace(/\/$/, ""));
}

async function request<T>(path: string, init?: RequestInit, query?: Record<string, any>): Promise<T> {
  const base = getBackendUrl();
  let url = `${base}${path}`;
  if (query) {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    });
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  const res = await fetch(url, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch { }
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json() as Promise<T>;
  return (await res.text()) as any;
}

// ===== Types =====
export interface HealthResponse {
  status: string; version: string; categories_loaded: number;
  llm_model: string; openai_key_configured: boolean;
}
export interface CategoryInfo { name: string; keyword_count: number; keywords: string[]; }
export interface ConfigResponse {
  llm_model: string; min_score_threshold: number; pdf_max_pages: number;
  poppler_path: string | null; categories: CategoryInfo[];
}
export interface DetectResponse {
  filename: string; pdf_type: string; is_digital: boolean; detection_time_sec: number;
}
export interface ExtractResponse {
  filename: string; pdf_type: string; char_count: number; rotation_info: string;
  text_preview: string; text_full: string; error: string; extraction_time_sec: number;
}
export interface ClassifyResponse {
  filename?: string; category: string; confidence_score: number; llm_score_0_10: number;
  pdf_type?: string; rotation_info?: string; classification_time_sec: number;
  error: string; extracted_text: string;
}
export interface OrganiseResponse {
  source_path: string; destination_path: string; category: string;
  action: string; dry_run: boolean;
}
export interface PipelineResultItem {
  file_name: string; original_path: string; pdf_type: string; category: string;
  llm_score: number; destination_folder: string; rotation_applied: string;
  processing_time: number; error: string;
}
export interface PipelineResponse {
  total_files: number; successful: number; failed: number;
  categories_found: Record<string, number>; total_time_sec: number;
  results: PipelineResultItem[];
}
export interface ReportRow {
  file_name: string; original_path: string; destination_folder: string;
  category: string; pdf_type?: string; confidence: string; processing_time: string; error: string;
}
export interface ReportResponse { report_path: string; total_rows: number; rows: ReportRow[]; }
export interface DriveStatusResponse {
  connected: boolean; drive_root: string; drive_input: string; drive_output: string;
  pdf_count: number; pdf_files: string[]; input_ok: boolean; output_ok: boolean;
}
export interface OneDriveStatusResponse {
  connected: boolean; onedrive_root: string; onedrive_input: string; onedrive_output: string;
  pdf_count: number; pdf_files: string[]; input_ok: boolean; output_ok: boolean;
}
export interface GoogleSetupResponse {
  status: string; oauth_configured: boolean; message: string;
}
export interface GoogleProfileResponse {
  authenticated: boolean; email: string | null; name: string | null; picture: string | null;
}
export interface GoogleFoldersResponse {
  status: string; parent_id: string; parent_name: string; folders: { id: string; name: string }[];
}

// ===== Endpoints =====
export const api = {
  health: () => request<HealthResponse>("/health"),
  getConfig: () => request<ConfigResponse>("/config"),
  listCategories: () => request<CategoryInfo[]>("/config/categories"),
  addCategory: (name: string, keywords: string[]) =>
    request<{ status: string; category: string; keywords: string[] }>("/config/categories", {
      method: "POST", body: JSON.stringify({ name, keywords }),
    }),

  detect: (file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return request<DetectResponse>("/detect", { method: "POST", body: fd });
  },

  extract: (file: File, opts: { max_pages?: number; force_ocr?: boolean; use_auto_rotation?: boolean }) => {
    const fd = new FormData(); fd.append("file", file);
    return request<ExtractResponse>("/extract", { method: "POST", body: fd }, opts);
  },

  classifyText: (text: string, llm_model?: string, threshold?: number) =>
    request<ClassifyResponse>("/classify/text", {
      method: "POST", body: JSON.stringify({ text, llm_model, threshold }),
    }),

  classifyPdf: (file: File, opts: {
    max_pages?: number; llm_model?: string; threshold?: number;
    force_ocr?: boolean; categories?: string;
  } = {}) => {
    const fd = new FormData(); fd.append("file", file);
    return request<ClassifyResponse>("/classify/pdf", { method: "POST", body: fd }, opts);
  },

  organise: (body: {
    source_path: string; category: string; output_folder: string;
    copy_mode?: boolean; dry_run?: boolean;
  }) => request<OrganiseResponse>("/organise", { method: "POST", body: JSON.stringify(body) }),

  pipeline: (body: {
    input_folder: string; output_folder: string; pdf_max_pages?: number;
    min_score?: number; llm_model?: string; copy_mode?: boolean; dry_run?: boolean;
  }) => request<PipelineResponse>("/pipeline/run", { method: "POST", body: JSON.stringify(body) }),

  getReport: (output_folder: string) =>
    request<ReportResponse>("/report", undefined, { output_folder }),

  downloadReportUrl: (output_folder: string) =>
    `${getBackendUrl()}/report/download?output_folder=${encodeURIComponent(output_folder)}`,

  driveStatus: (input_folder?: string) =>
    request<DriveStatusResponse>("/drive/status", undefined, input_folder ? { input_folder } : undefined),

  driveClassify: (body: {
    drive_input_folder?: string; drive_output_folder?: string;
    copy_mode?: boolean; dry_run?: boolean; pdf_max_pages?: number;
    min_score?: number; llm_model?: string;
  }) => request<any>("/drive/classify", { method: "POST", body: JSON.stringify(body) }),

  googleCheckSetup: () =>
    request<GoogleSetupResponse>("/google/check-setup"),

  googleProfile: () =>
    request<GoogleProfileResponse>("/google/profile"),

  googleDriveFolders: (parent_id: string = "root") =>
    request<GoogleFoldersResponse>(`/google/drive/folders`, undefined, { parent_id }),

  googleDriveClassify: (body: {
    drive_input_folder_id: string; drive_output_folder_id: string;
    copy_mode?: boolean; dry_run?: boolean; pdf_max_pages?: number;
    min_score?: number; llm_model?: string; max_files?: number;
  }) => request<any>("/google/drive/classify", { method: "POST", body: JSON.stringify(body) }),

  onedriveStatus: (input_folder?: string) =>
    request<OneDriveStatusResponse>("/onedrive/status", undefined, input_folder ? { input_folder } : undefined),

  onedriveClassify: (body: {
    onedrive_input_folder?: string; onedrive_output_folder?: string;
    copy_mode?: boolean; dry_run?: boolean; pdf_max_pages?: number;
    min_score?: number; llm_model?: string;
  }) => request<any>("/onedrive/classify", { method: "POST", body: JSON.stringify(body) }),

  onedriveCheckSetup: () =>
    request<GoogleSetupResponse>("/onedrive/check-setup"),

  onedriveProfile: () =>
    request<GoogleProfileResponse>("/onedrive/profile"),

  onedriveFolders: (parent_id: string = "root") =>
    request<GoogleFoldersResponse>(`/onedrive/folders`, undefined, { parent_id }),

  onedriveCloudClassify: (body: {
    onedrive_input_folder_id: string; onedrive_output_folder_id: string;
    copy_mode?: boolean; dry_run?: boolean; pdf_max_pages?: number;
    min_score?: number; llm_model?: string; max_files?: number;
  }) => request<any>("/onedrive/drive/classify", { method: "POST", body: JSON.stringify(body) }),

  selectFolder: () => request<{ path: string | null }>("/select-folder"),

  listDirectories: (path?: string) =>
    request<{
      current_path: string;
      parent_path: string | null;
      subdirectories: { name: string; path: string }[];
      drives: string[];
    }>("/list-directories", undefined, path ? { path } : undefined),
};
