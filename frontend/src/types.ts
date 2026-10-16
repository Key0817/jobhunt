// src/types.ts
export type JobStatus = 'Guardada' | 'Aplicada' | 'Entrevista' | 'Oferta' | 'Rechazada';

export interface Job {
  id: string;
  title: string;
  company: string;
  status: JobStatus;
  score: number;
}