
import { useEffect, useRef, useState } from "react";
import {
    ArrowLeft,
    BriefcaseBusiness,
    Building2,
    CalendarDays,
    Download,
    FileText,
    LoaderCircle,
    Mail,
    Copy,
} from "lucide-react";

const API_URL = "http://localhost:8000";

type CVDetail = {
    id: string;
    job_id: string;
    job_title: string;
    company: string | null;
    variant: string;
    profile_text: string;
    cover_letter: string | null;
    cover_letter_model_name: string | null;
    profile_key: string;
    status: string;
    approved: boolean;
    model_name: string | null;
    created_at: string;
};

type CVDetailPageProps = {
    versionId: string;
    onBack: () => void;
};

const STATUS_LABELS: Record<string, string> = {
    pending_review: "Pendiente de revisión",
    approved: "Aprobado",
    rejected: "Rechazado",
    archived: "Archivado",
};

export default function CVDetailPage({
    versionId,
    onBack,
}: CVDetailPageProps) {
    const [cv, setCv] = useState<CVDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [downloadingPDF, setDownloadingPDF] = useState(false);
    const [pdfError, setPdfError] = useState<string | null>(null);

    const [generatingLetter, setGeneratingLetter] = useState(false);
    const [letterError, setLetterError] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const letterRequest = useRef<AbortController | null>(null);
    const statusRequest = useRef<AbortController | null>(null);
    const [savingStatus, setSavingStatus] = useState(false);
    const [statusError, setStatusError] = useState<string | null>(null);
    const [statusSaved, setStatusSaved] = useState(false);

    async function handleStatusChange(status: string) {
        if (!cv || status === cv.status) return;
        const controller = new AbortController();
        statusRequest.current?.abort();
        statusRequest.current = controller;
        setSavingStatus(true);
        setStatusError(null);
        setStatusSaved(false);
        try {
            const response = await fetch(
                `${API_URL}/api/cvs/${encodeURIComponent(versionId)}/status`,
                { method: "PATCH", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ status }), signal: controller.signal }
            );
            const data = await response.json().catch(() => null);
            if (!response.ok) {
                throw new Error(data?.detail === "Not Found"
                    ? "El servidor no tiene la ruta para cambiar el estado. Reinicia el backend actualizado."
                    : typeof data?.detail === "string" ? data.detail : "No se pudo guardar el estado.");
            }
            if (!controller.signal.aborted) {
                setCv(current => current && current.id === data.id ? { ...current, status: data.status, approved: data.approved } : current);
                setStatusSaved(true);
            }
        } catch (err) {
            if (!controller.signal.aborted) {
                setStatusError(err instanceof Error ? err.message : "No se pudo guardar el estado.");
            }
        } finally {
            if (!controller.signal.aborted) setSavingStatus(false);
        }
    }

    async function handleGenerateLetter() {
        const controller = new AbortController();
        letterRequest.current?.abort();
        letterRequest.current = controller;
        setGeneratingLetter(true);
        setLetterError(null);
        setCopied(false);
        try {
            const response = await fetch(
                `${API_URL}/api/cvs/${encodeURIComponent(versionId)}/cover-letter`,
                { method: "POST", signal: controller.signal }
            );
            const data = await response.json().catch(() => null);
            if (!response.ok) {
                throw new Error(data?.detail === "Not Found"
                    ? "El servidor no tiene la ruta de cover letter. Reinicia el backend actualizado."
                    : typeof data?.detail === "string" ? data.detail : "No se pudo generar la carta de presentación.");
            }
            if (!controller.signal.aborted) {
                setCv(current => current && current.id === data.id ? { ...current, ...data } : current);
            }
        } catch (err) {
            if (!controller.signal.aborted) {
                setLetterError(err instanceof Error ? err.message : "No se pudo generar la carta.");
            }
        } finally {
            if (!controller.signal.aborted) setGeneratingLetter(false);
        }
    }

    async function handleCopyLetter() {
        if (!cv?.cover_letter) return;
        try {
            await navigator.clipboard.writeText(cv.cover_letter);
            setCopied(true);
        } catch {
            setLetterError("No se pudo copiar. Selecciona y copia el texto de la carta.");
        }
    }

    async function handleDownloadPDF() {
        setDownloadingPDF(true);
        setPdfError(null);

        try {
            const response = await fetch(
                `${API_URL}/api/cvs/${encodeURIComponent(versionId)}/pdf`
            );

            if (!response.ok) {
                const error = await response.json().catch(() => null);
                throw new Error(error?.detail || "No se pudo generar el PDF.");
            }

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");

            link.href = url;
            link.download = `cv_${versionId}.pdf`;
            document.body.appendChild(link);
            link.click();
            link.remove();

            URL.revokeObjectURL(url);
        } catch (err) {
            setPdfError(
                err instanceof Error ? err.message : "Error al descargar el PDF."
            );
        } finally {
            setDownloadingPDF(false);
        }
    }

    useEffect(() => {
        const controller = new AbortController();
        setGeneratingLetter(false);
        setLetterError(null);
        setCopied(false);
        setSavingStatus(false);
        setStatusError(null);
        setStatusSaved(false);
        setPdfError(null);

        async function loadCV() {
            setLoading(true);
            setError(null);

            try {
                const response = await fetch(
                    `${API_URL}/api/cvs/${encodeURIComponent(versionId)}`,
                    { signal: controller.signal }
                );

                if (!response.ok) {
                    throw new Error(
                        response.status === 404
                            ? "No se encontró esta versión del CV."
                            : "No se pudo cargar el CV."
                    );
                }

                const data: CVDetail = await response.json();

                setCv(data);
            } catch (err) {
                if (err instanceof Error && err.name === "AbortError") {
                    return;
                }

                setError(
                    err instanceof Error
                        ? err.message
                        : "Ocurrió un error inesperado."
                );
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false);
                }
            }
        }

        loadCV();

        return () => {
            controller.abort();
            letterRequest.current?.abort();
            statusRequest.current?.abort();
        };
    }, [versionId]);

    if (loading) {
        return (
            <div className="flex min-h-64 items-center justify-center gap-3 text-slate-500">
                <LoaderCircle className="h-5 w-5 animate-spin" />
                Cargando CV...
            </div>
        );
    }

    if (error || !cv) {
        return (
            <section className="space-y-4 rounded-xl border border-red-200 bg-white p-6">
                <button
                    onClick={onBack}
                    className="inline-flex items-center gap-2 text-sm text-slate-600 hover:text-slate-900"
                >
                    <ArrowLeft className="h-4 w-4" />
                    Volver a mis CV
                </button>

                <p role="alert" className="text-sm text-red-600">
                    {error ?? "No se pudo cargar el CV."}
                </p>
            </section>
        );
    }

    return (
        <main className="mx-auto w-full max-w-5xl space-y-6">
            <button
                onClick={onBack}
                className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 transition hover:text-slate-900"
            >
                <ArrowLeft className="h-4 w-4" />
                Volver a mis CV
            </button>

            <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                    <div className="flex items-start gap-3">
                        <div className="rounded-xl bg-blue-50 p-3 text-blue-700">
                            <FileText className="h-6 w-6" />
                        </div>

                        <div>
                            <h1 className="text-2xl font-bold text-slate-900">
                                Detalle del CV
                            </h1>
                            <p className="mt-1 text-sm text-slate-500">
                                Revisa el perfil adaptado a esta oferta.
                            </p>
                        </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-3">
                        <div className="space-y-1">
                            <label htmlFor="cv-status" className="block text-xs font-medium text-slate-500">Estado del CV</label>
                            <select
                                id="cv-status"
                                value={cv.status}
                                onChange={event => handleStatusChange(event.target.value)}
                                disabled={savingStatus}
                                className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:opacity-60"
                            >
                                {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                            </select>
                            {savingStatus && <p role="status" className="text-xs text-slate-500">Guardando estado...</p>}
                            {statusSaved && !savingStatus && <p role="status" className="text-xs text-emerald-700">Estado guardado</p>}
                            {statusError && <p role="alert" className="max-w-xs text-xs text-red-600">{statusError}</p>}
                        </div>
                    </div>
                </div>

                <div className="mt-6 grid gap-4 border-t border-slate-100 pt-5 sm:grid-cols-2">
                    <div className="flex items-start gap-3">
                        <BriefcaseBusiness className="mt-1 h-5 w-5 text-slate-400" />
                        <div>
                            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                                Puesto
                            </p>
                            <p className="mt-1 font-medium text-slate-900">
                                {cv.job_title || "Puesto no disponible"}
                            </p>
                        </div>
                    </div>

                    <div className="flex items-start gap-3">
                        <Building2 className="mt-1 h-5 w-5 text-slate-400" />
                        <div>
                            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                                Empresa
                            </p>
                            <p className="mt-1 font-medium text-slate-900">
                                {cv.company || "Empresa no especificada"}
                            </p>
                        </div>
                    </div>

                    <div className="flex items-start gap-3">
                        <CalendarDays className="mt-1 h-5 w-5 text-slate-400" />
                        <div>
                            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                                Fecha de creación
                            </p>
                            <p className="mt-1 text-sm text-slate-700">
                                {new Date(cv.created_at).toLocaleString()}
                            </p>
                        </div>
                    </div>

                    <div>
                        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                            Variante
                        </p>
                        <p className="mt-1 text-sm text-slate-700">
                            {cv.variant || "General"}
                        </p>
                    </div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="mb-4 flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-900">
                            Perfil profesional
                        </h2>
                        <p className="mt-1 text-sm text-slate-500">
                            Perfil guardado en esta versión del CV. Se utiliza para el PDF y la carta de presentación.
                        </p>
                    </div>

                </div>

                <label
                    htmlFor="cv-profile-text"
                    className="mb-2 block text-sm font-medium text-slate-700"
                >
                    Contenido del perfil
                </label>

                <textarea
                    id="cv-profile-text"
                    value={cv.profile_text ?? ""}
                    readOnly
                    rows={12}
                    className="w-full resize-y rounded-xl border border-slate-300 bg-white p-4 text-sm leading-7 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    placeholder="El perfil profesional de esta versión aparecerá aquí."
                />

                <div className="mt-2 flex justify-between text-xs text-slate-500">
                    <span>Contenido guardado</span>
                    <span>{(cv.profile_text ?? "").length} caracteres</span>
                </div>

                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-5">
                    <button
                        type="button"
                        onClick={handleGenerateLetter}
                        disabled={generatingLetter}
                        className="inline-flex items-center gap-2 rounded-lg border border-blue-200 px-3 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-50 disabled:cursor-wait disabled:opacity-60"
                    >
                        {generatingLetter ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Mail className="h-4 w-4" />}
                        {generatingLetter ? "Generando carta..." : cv.cover_letter ? "Regenerar cover letter" : "Generar cover letter"}
                    </button>
                    <button
                        type="button"
                        onClick={handleDownloadPDF}
                        disabled={downloadingPDF}
                        className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-wait disabled:opacity-60"
                    >
                        {downloadingPDF ? (
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                        ) : (
                            <Download className="h-4 w-4" />
                        )}

                        {downloadingPDF ? "Generando PDF..." : "Descargar PDF"}
                    </button>
                </div>
                {pdfError && <p role="alert" className="mt-3 text-sm text-red-600">{pdfError}</p>}
            </section>

            <section aria-busy={generatingLetter} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-900">Carta de presentación</h2>
                        <p className="mt-1 text-sm text-slate-500">
                            Se genera con la oferta asociada y el perfil guardado de este CV. Se guarda automáticamente.
                        </p>
                    </div>
                    {cv.cover_letter && (
                        <button type="button" onClick={handleCopyLetter} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50">
                            <Copy className="h-4 w-4" />{copied ? "Copiada" : "Copiar"}
                        </button>
                    )}
                </div>
                {letterError && <p role="alert" className="mt-3 text-sm text-red-600">{letterError}</p>}
                {generatingLetter && <p role="status" className="mt-4 text-sm text-blue-700">Redactando la carta para {cv.job_title}...</p>}
                {cv.cover_letter ? (
                    <div className="mt-4 whitespace-pre-wrap rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm leading-7 text-slate-800">{cv.cover_letter}</div>
                ) : (
                    <p className="mt-4 text-sm text-slate-500">Usa «Generar cover letter» para crear una carta para esta oferta.</p>
                )}
            </section>
        </main>
    );
}
