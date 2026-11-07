import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/endpoints";
import type { SearchHitDto } from "../api/types";
import { Drawer } from "../components/Drawer";
import { Icon } from "../components/Icon";
import {
  DefinitionItem,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../components/Primitives";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { formatDateTime } from "../lib/format";
import { safeDocumentUrl } from "../lib/safe";

function SourceLink({ url, label }: { url: string; label: string }) {
  const safe = safeDocumentUrl(url);
  if (!safe) return <span className="part-meta">{url}</span>;
  return (
    <a className="text-link" href={safe} target="_blank" rel="noopener noreferrer">
      {label}
      <Icon name="external" size={14} />
    </a>
  );
}

function SearchHit({ hit, onOpen }: { hit: SearchHitDto; onOpen: () => void }) {
  const { t } = useI18n();
  return (
    <li className="search-hit">
      <div className="search-hit-head">
        <div>
          <h3>{hit.title}</h3>
          <p className="part-meta">
            {hit.section ? `${hit.section} · ` : ""}
            {t("docs.version")} {hit.version}
          </p>
        </div>
        <button type="button" className="btn btn-secondary btn-small" onClick={onOpen}>
          {t("docs.openDetail")}
        </button>
      </div>
      <blockquote className="citation-snippet">{hit.snippet}</blockquote>
      <div className="search-hit-foot">
        <SourceLink url={hit.source_url} label={t("docs.sourceLink")} />
      </div>
    </li>
  );
}

function DocumentDrawer({
  documentId,
  onClose,
}: {
  documentId: string | null;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const query = useApiQuery(
    (signal) => (documentId ? api.getDoc(documentId, signal) : Promise.resolve(null)),
    [documentId],
    { enabled: Boolean(documentId) },
  );

  return (
    <Drawer
      open={Boolean(documentId)}
      onClose={onClose}
      title={t("docs.detail")}
      closeLabel={t("docs.closeDetail")}
    >
      <p className="help-text">{t("docs.detailHelp")}</p>
      {query.status === "error" ? (
        <ErrorState message={t("docs.loadDetailError")} onRetry={query.reload} />
      ) : !query.data ? (
        <LoadingState />
      ) : (
        <>
          <h3>{query.data.title}</h3>
          <dl className="definition-grid">
            <DefinitionItem label={t("docs.documentId")} mono>
              {query.data.document_id}
            </DefinitionItem>
            <DefinitionItem label={t("docs.version")}>{query.data.version}</DefinitionItem>
            <DefinitionItem label={t("docs.language")}>{query.data.language}</DefinitionItem>
            <DefinitionItem label={t("docs.section")}>{query.data.section ?? "—"}</DefinitionItem>
            <DefinitionItem label={t("docs.visibility")}>{query.data.visibility}</DefinitionItem>
            <DefinitionItem label={t("docs.ingested")}>
              {formatDateTime(query.data.ingested_at, locale)}
            </DefinitionItem>
            <DefinitionItem label={t("docs.license")}>
              {query.data.license_note ?? "—"}
            </DefinitionItem>
          </dl>
          <SourceLink url={query.data.source_url} label={t("docs.viewSource")} />
        </>
      )}
    </Drawer>
  );
}

export function DocsPage() {
  const { t, locale } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedDocId = searchParams.get("doc");
  const [searchInput, setSearchInput] = useState("");
  const [queryText, setQueryText] = useState("");

  const docsQuery = useApiQuery((signal) => api.listDocs(signal), []);
  const searchQuery = useApiQuery(
    (signal) => (queryText ? api.searchDocs(queryText, signal) : Promise.resolve(null)),
    [queryText],
    { enabled: Boolean(queryText) },
  );

  const openDoc = (documentId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("doc", documentId);
    setSearchParams(next, { replace: true });
  };

  const closeDoc = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("doc");
    setSearchParams(next, { replace: true });
  };

  const runSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setQueryText(searchInput.trim());
  };

  return (
    <section className="page" aria-labelledby="docs-title">
      <PageHeader
        title={t("docs.title")}
        description={t("docs.subtitle")}
        actions={
          <button type="button" className="btn btn-secondary" onClick={docsQuery.reload}>
            <Icon name="refresh" size={16} />
            {t("app.refresh")}
          </button>
        }
      />

      <section className="panel search-panel" aria-labelledby="docs-search-heading">
        <h2 id="docs-search-heading">{t("docs.searchLabel")}</h2>
        <form className="search-form" onSubmit={runSearch} role="search">
          <label className="visually-hidden" htmlFor="docs-search">
            {t("docs.searchLabel")}
          </label>
          <div className="search-input-row">
            <input
              id="docs-search"
              type="search"
              value={searchInput}
              maxLength={2000}
              placeholder={t("docs.searchPlaceholder")}
              onChange={(event) => setSearchInput(event.target.value)}
            />
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!searchInput.trim() || searchQuery.status === "loading"}
            >
              <Icon name="search" size={16} />
              {searchQuery.status === "loading" ? t("docs.searching") : t("docs.search")}
            </button>
          </div>
        </form>
        <p className="help-text">{t("docs.searchHint")}</p>

        {queryText ? (
          <div className="search-results" aria-live="polite">
            <div className="result-heading">
              <h3>{t("docs.resultsFor", { query: queryText })}</h3>
              {searchQuery.data ? (
                <span className="part-meta">
                  {t("docs.resultCount", { count: searchQuery.data.hits.length })}
                </span>
              ) : null}
            </div>
            {searchQuery.status === "loading" ? (
              <LoadingState />
            ) : searchQuery.status === "error" ? (
              <ErrorState message={t("docs.searchError")} onRetry={searchQuery.reload} />
            ) : (searchQuery.data?.hits.length ?? 0) === 0 ? (
              <EmptyState title={t("docs.noResults")} />
            ) : (
              <ol className="search-hit-list">
                {searchQuery.data?.hits.map((hit) => (
                  <SearchHit
                    key={hit.chunk_id}
                    hit={hit}
                    onOpen={() => openDoc(hit.document_id)}
                  />
                ))}
              </ol>
            )}
          </div>
        ) : null}
      </section>

      <section className="panel" aria-labelledby="docs-library-heading">
        <div className="result-heading">
          <h2 id="docs-library-heading">{t("docs.documents")}</h2>
          {docsQuery.data ? (
            <span className="part-meta">
              {t("docs.resultCount", { count: docsQuery.data.items.length })}
            </span>
          ) : null}
        </div>

        {docsQuery.initialLoading ? (
          <LoadingState />
        ) : docsQuery.status === "error" ? (
          <ErrorState message={t("docs.documentsError")} onRetry={docsQuery.reload} />
        ) : (docsQuery.data?.items.length ?? 0) === 0 ? (
          <EmptyState title={t("docs.noDocuments")} />
        ) : (
          <div className="table-shell">
            <table className="data-table">
              <caption className="visually-hidden">{t("docs.documents")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("resources.col.name")}</th>
                  <th scope="col">{t("docs.section")}</th>
                  <th scope="col">{t("docs.version")}</th>
                  <th scope="col">{t("docs.language")}</th>
                  <th scope="col">{t("docs.ingested")}</th>
                  <th scope="col">{t("app.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {docsQuery.data?.items.map((document) => (
                  <tr key={document.document_id}>
                    <td>{document.title}</td>
                    <td>{document.section ?? "—"}</td>
                    <td>{document.version}</td>
                    <td>{document.language}</td>
                    <td className="nowrap">{formatDateTime(document.ingested_at, locale)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        onClick={() => openDoc(document.document_id)}
                        aria-label={`${t("docs.openDetail")}: ${document.title}`}
                      >
                        {t("docs.openDetail")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <DocumentDrawer documentId={selectedDocId} onClose={closeDoc} />
    </section>
  );
}
