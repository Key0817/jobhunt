// Estados del flujo, en el mismo orden y con los mismos nombres que usa la base de datos.
export const STATUSES = [
  'encontrada',
  'aplicable',
  'cv_listo',
  'solicitada',
  'entrevista',
  'oferta',
  'rechazada',
  'descartada',
] as const;

export type JobStatus = (typeof STATUSES)[number];

export const STATUS_LABELS: Record<JobStatus, string> = {
  encontrada: 'Encontradas',
  aplicable: 'Aplicables',
  cv_listo: 'CV listo',
  solicitada: 'Postuladas',
  entrevista: 'Entrevista',
  oferta: 'Oferta',
  rechazada: 'Rechazadas',
  descartada: 'Descartadas',
};

export interface Job {
  id: string;
  title: string;
  company: string;
  status: JobStatus;
  score: number;
  url: string;
  source: string;
  country: string | null;
  location: string | null;
  posted_at: string | null;
  applied_at: string | null;
}

export interface WeeklyCount {
  week: string; 
  count: number;
}

export interface Stats {
  total_jobs: number;
  new_last_7_days: number;
  applied: number;
  interviews: number;
  offers: number;
  by_status: Record<JobStatus, number>;
  weekly_applications: WeeklyCount[];
}