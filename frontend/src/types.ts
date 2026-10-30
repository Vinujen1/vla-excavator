// Mirrors app/schemas.py and the telemetry snapshot in app/main.py.

export interface BBox { x1: number; y1: number; x2: number; y2: number }

export interface DepthEstimate {
  status: "ok" | "inconsistent" | "no_valid_depth" | "unavailable";
  depth_m: number | null;
  spread_m: number | null;
  valid_fraction: number;
  roi: BBox | null;
  xyz_camera_m: [number, number, number] | null;
  reason: string | null;
}

export interface Detection {
  id: string;
  source: "yolo" | "color_marker";
  label: string;
  class_id: number | null;
  confidence: number | null;
  bbox: BBox;
  depth: DepthEstimate;
}

export interface DetectionFrame {
  frame_number: number;
  frame_time: number;
  inference_ms: number | null;
  marker_ms: number | null;
  detections: Detection[];
}

export interface MarkerSettings {
  enabled: boolean; name: string;
  h_min: number; h_max: number; s_min: number; s_max: number; v_min: number; v_max: number;
  min_area_px: number;
}

export interface DetectionSettings { yolo_enabled: boolean; confidence: number; marker: MarkerSettings }

export interface Target {
  detection: Detection;
  selected_at: number;
  first_frame_number: number;
  last_seen_frame_number: number;
  last_seen_time: number;
  age_s: number;
  stale: boolean;
  stale_reason: string | null;
  association: string;
}

export interface CameraState {
  status: string;
  error: string | null;
  device: { name?: string; serial?: string; firmware?: string; usb?: string };
  configured: { width: number; height: number; fps: number };
  measured_fps: number;
  intrinsics: Record<string, unknown> | null;
  seq?: number;
  frame_time_unix?: number;
  frame_age_s?: number;
}

export interface DetectionState {
  model_status: string;
  model_error: string | null;
  model: { file?: string; device?: string; gpu?: string | null; half?: boolean; imgsz?: number; classes?: string[]; dataset_note?: string };
  detection_fps: number;
  inference_ms: number | null;
  marker_ms: number | null;
  frames_skipped_while_busy: number;
  max_fps: number;
  settings: DetectionSettings;
  frame: DetectionFrame | null;
  frame_age_s: number | null;
}

export interface StopAck {
  sent_at: number; source: string; reason: string; acknowledged: boolean; ack_latency_ms: number | null;
  pi_response: { latched: string; outputs_off: boolean } | null; error: string | null; attempts: number;
}

export interface CommandResult {
  handled: boolean; kind: string; message: string; source: string; at: number;
  needs_confirmation?: boolean; ambiguous?: string[]; stop_ack?: StopAck;
}

export interface Transcript {
  text: string; model: string; processing_s: number; transcribed_at_unix: number; source: string;
  source_label: string; recording: string | null; low_confidence?: boolean; dispatch?: CommandResult;
}

export interface VoiceState {
  state: "idle" | "recording" | "listening" | "transcribing" | "error";
  source: "jetson_mic" | "browser_mic" | null;
  source_label: string | null;
  error: string | null;
  recording: Record<string, unknown> | null;
  transcript: Transcript | null;
  recording_elapsed_s: number | null;
  listening: { device: string; started: number; max_s: number; chunks: number; elapsed_s: number } | null;
  max_record_s: number;
  whisper_model: string;
}

export interface TaskStep { index: number; action: string; description: string; parameters: Record<string, unknown> }

export interface TaskProposal {
  id: string; created_at: number; provider: string; provider_kind: "reasoning_model" | "demo_planner";
  observation_id: string; instruction: string; target_summary: string | null;
  steps: TaskStep[]; warnings: string[]; assumptions: string[]; executable: false;
}

export interface ProviderStatus { id: string; name: string; kind: string; available: boolean; message: string }

export interface PiJoint {
  position: number | null; feedback: string | null; units: string | null; feedback_age_s: number | null;
  limits: [number, number] | null; max_duty: number;
}

export interface PiState {
  backend: "simulation" | "hardware"; pins_verified: boolean; commissioning: boolean;
  state: string; latched: string | null; fault: { code: string; message: string; at: number } | null;
  current_action: { action_id: string; kind: string; params: Record<string, unknown> } | null;
  joints: Record<string, PiJoint>; energised: boolean;
  heartbeat: { session: string | null; age_s: number | null; clock_offset_ms: number | null; watchdog_s: number };
  pwm_notes: string[]; uptime_s: number;
}

export interface HardwareState {
  pi: {
    status: string; endpoint_configured: boolean; error: string | null; last_contact: number | null;
    state_age_s: number | null; heartbeat_rtt_ms: number | null; session: string; fresh: boolean;
  };
  state: PiState | null;
  last_stop: StopAck | null;
}

export interface DecisionProviderStatus {
  id: string; name: string; kind: string; available: boolean; verified: boolean; message: string;
  endpoint?: string | null; model?: string; reported_model?: string | null; last_ok_at?: number | null;
  last_latency_ms?: number | null; last_error?: string | null; busy?: boolean; requests?: number;
}

export interface TargetRef { detection_id: string; label: string; source: string; frame_number: number; depth_status: string; depth_m: number | null }

export interface TaskSpec {
  task_id: string; goal: string; instruction: { text: string; source: string }; observation_id: string;
  mode: "simulation" | "hardware"; provider: string; cycles: number; timeout_s: number; target: TargetRef | null;
  plan: string[]; explanation: string; warnings: string[]; start_requested: boolean; created_at: number; approved_at: number | null;
}

export interface DecisionRecord {
  at: number; kind: "goal" | "skill"; provider: string; provider_kind: string; observation_id: string;
  latency_ms: number; output: Record<string, unknown> | null; error: string | null; discarded: boolean;
  verdict?: string; expected?: string | null; step?: number;
}

export type SupervisorStateName = "DISARMED" | "READY" | "PLANNING" | "AWAITING_APPROVAL" | "EXECUTING" | "VERIFYING"
  | "PAUSED" | "COMPLETED" | "FAULT" | "STOPPED";

export interface SupervisorState {
  state: SupervisorStateName; reason: string | null; execution_mode: string | null;
  task: TaskSpec | null;
  pending: {
    spec: TaskSpec | null; proposal: Record<string, unknown>; errors: string[]; warnings: string[];
    provider: string; provider_kind: string; latency_ms: number; observation_id: string; blockers: string[];
  } | null;
  progress: {
    cycle: number; current_skill: string | null;
    current_move: { joint: string; target: number; pose: string; index: number; of: number } | null;
    completed_skills: number; total_skills: number; started_at: number | null; active_s: number;
    retries: Record<string, number>; last_result: Record<string, unknown> | null; pausing: boolean; cancelling: boolean;
  };
  decisions: DecisionRecord[];
  blockers: string[];
  last_plan_error: string | null;
}

export interface LogEvent { seq: number; time: number; level: "info" | "warn" | "error"; source: string; message: string }

export interface Telemetry {
  time: number;
  mode: { id: "hardware" | "simulation" | "perception_preview"; label: string };
  camera: CameraState;
  detection: DetectionState;
  target: Target | null;
  voice: VoiceState;
  planner: { providers: ProviderStatus[]; proposal: TaskProposal | null; error: string | null; observation_id: string | null };
  hardware: HardwareState;
  supervisor: SupervisorState;
  decision_providers: DecisionProviderStatus[];
  calibration: Record<string, { file: string; loaded: boolean; error: string | null; verified?: boolean; camera_to_base?: boolean }>;
  command: CommandResult | null;
  session_log: string | null;
  events: LogEvent[];
}

export interface AudioDevice { id: string; label: string; is_dji: boolean }
