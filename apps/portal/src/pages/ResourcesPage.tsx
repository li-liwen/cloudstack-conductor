import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/endpoints";
import type {
  InstanceResource,
  NetworkResource,
  OfferingResource,
  TemplateResource,
  VolumeResource,
} from "../api/types";
import { Drawer } from "../components/Drawer";
import { Icon } from "../components/Icon";
import {
  Alert,
  DefinitionItem,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  Spinner,
} from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { Tabs, TabPanel } from "../components/Tabs";
import { useConfig } from "../config/ConfigProvider";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { safeSameOriginPath } from "../lib/safe";

const TAB_IDS = ["instances", "volumes", "networks", "offerings", "templates"] as const;
type TabId = (typeof TAB_IDS)[number];

function isTabId(value: string | null): value is TabId {
  return Boolean(value && (TAB_IDS as readonly string[]).includes(value));
}

function StateCell({ state }: { state: string }) {
  return <StatusBadge label={state} />;
}

function InstancesTable({
  rows,
  onDetails,
}: {
  rows: InstanceResource[];
  onDetails: (instance: InstanceResource) => void;
}) {
  const { t, locale } = useI18n();
  return (
    <table className="data-table">
      <caption className="visually-hidden">{t("resources.tab.instances")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("resources.col.name")}</th>
          <th scope="col">{t("resources.col.state")}</th>
          <th scope="col">{t("resources.col.offering")}</th>
          <th scope="col">{t("resources.col.zone")}</th>
          <th scope="col">{t("resources.col.ip")}</th>
          <th scope="col">{t("resources.col.created")}</th>
          <th scope="col">{t("app.actions")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              <button type="button" className="text-link button-link" onClick={() => onDetails(row)}>
                {row.name}
              </button>
              <span className="part-meta mono break-all">{row.id}</span>
            </td>
            <td><StateCell state={row.state} /></td>
            <td>{row.offering_name ?? "—"}</td>
            <td>{row.zone_name ?? row.zone_id ?? "—"}</td>
            <td>{row.ip_addresses.length > 0 ? row.ip_addresses.join(", ") : "—"}</td>
            <td className="nowrap">{formatDateTime(row.created, locale)}</td>
            <td>
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => onDetails(row)}
                aria-label={`${t("resources.col.details")}: ${row.name}`}
              >
                {t("resources.col.details")}
                <Icon name="chevronRight" size={14} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function VolumesTable({ rows }: { rows: VolumeResource[] }) {
  const { t, locale } = useI18n();
  return (
    <table className="data-table">
      <caption className="visually-hidden">{t("resources.tab.volumes")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("resources.col.name")}</th>
          <th scope="col">{t("resources.col.size")}</th>
          <th scope="col">{t("resources.col.type")}</th>
          <th scope="col">{t("resources.col.state")}</th>
          <th scope="col">{t("resources.col.attachedTo")}</th>
          <th scope="col">{t("resources.col.zone")}</th>
          <th scope="col">{t("resources.col.created")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              {row.name}
              <span className="part-meta mono break-all">{row.id}</span>
            </td>
            <td>{t("resources.sizeGiB", { value: row.size_gib })}</td>
            <td>{row.type}</td>
            <td><StateCell state={row.state} /></td>
            <td>{row.vm_name ?? "—"}</td>
            <td>{row.zone_id ?? "—"}</td>
            <td className="nowrap">{formatDateTime(row.created, locale)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NetworksTable({ rows }: { rows: NetworkResource[] }) {
  const { t } = useI18n();
  return (
    <table className="data-table">
      <caption className="visually-hidden">{t("resources.tab.networks")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("resources.col.name")}</th>
          <th scope="col">{t("resources.col.type")}</th>
          <th scope="col">{t("resources.col.state")}</th>
          <th scope="col">{t("resources.col.cidr")}</th>
          <th scope="col">{t("resources.col.gateway")}</th>
          <th scope="col">{t("resources.col.vlan")}</th>
          <th scope="col">{t("resources.col.default")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              {row.name}
              <span className="part-meta mono break-all">{row.id}</span>
            </td>
            <td>{row.type}</td>
            <td><StateCell state={row.state} /></td>
            <td className="mono">{row.cidr ?? "—"}</td>
            <td className="mono">{row.gateway ?? "—"}</td>
            <td>{row.vlan ?? "—"}</td>
            <td>{row.is_default ? t("resources.yes") : t("resources.no")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OfferingsTable({ rows }: { rows: OfferingResource[] }) {
  const { t } = useI18n();
  return (
    <table className="data-table">
      <caption className="visually-hidden">{t("resources.tab.offerings")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("resources.col.name")}</th>
          <th scope="col">{t("resources.col.vcpu")}</th>
          <th scope="col">{t("resources.col.memory")}</th>
          <th scope="col">{t("resources.col.speed")}</th>
          <th scope="col">{t("resources.col.gpu")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              {row.name}
              <span className="part-meta mono break-all">{row.id}</span>
            </td>
            <td>{row.cpu_count}</td>
            <td>{t("resources.sizeGiB", { value: row.memory_gib })}</td>
            <td>{row.cpu_speed_mhz ? `${row.cpu_speed_mhz} MHz` : "—"}</td>
            <td>{row.gpu_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TemplatesTable({ rows }: { rows: TemplateResource[] }) {
  const { t } = useI18n();
  return (
    <table className="data-table">
      <caption className="visually-hidden">{t("resources.tab.templates")}</caption>
      <thead>
        <tr>
          <th scope="col">{t("resources.col.name")}</th>
          <th scope="col">{t("resources.col.os")}</th>
          <th scope="col">{t("resources.col.format")}</th>
          <th scope="col">{t("resources.col.hypervisor")}</th>
          <th scope="col">{t("resources.col.ready")}</th>
          <th scope="col">{t("resources.col.public")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              {row.name}
              <span className="part-meta mono break-all">{row.id}</span>
            </td>
            <td>{row.os_type}</td>
            <td>{row.format}</td>
            <td>{row.hypervisor}</td>
            <td>{row.ready ? t("resources.yes") : t("resources.no")}</td>
            <td>{row.public ? t("resources.yes") : t("resources.no")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function InstanceDrawer({
  instanceId,
  fallback,
  onClose,
}: {
  instanceId: string;
  fallback: InstanceResource | null;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const { config } = useConfig();
  const query = useApiQuery(
    (signal) => (instanceId ? api.getInstance(instanceId, signal) : Promise.resolve(null)),
    [instanceId],
    { enabled: Boolean(instanceId) },
  );
  const [opening, setOpening] = useState(false);
  const [consoleError, setConsoleError] = useState<string | null>(null);
  const instance =
    query.data && query.data.id === instanceId ? query.data : fallback;

  const openConsole = async () => {
    if (!instance || opening) return;
    setOpening(true);
    setConsoleError(null);
    try {
      const launch = await api.openConsole(instance.id);
      const url = safeSameOriginPath(launch.launch_url);
      if (!url) throw new Error("invalid launch url");
      // The launch URL is a signed, one-time application path. Navigating in
      // the same tab avoids popup blockers; the console page links back.
      window.location.assign(url);
    } catch (error) {
      setConsoleError(userErrorMessage(error, t));
    } finally {
      setOpening(false);
    }
  };

  return (
    <Drawer
      open={Boolean(instanceId)}
      onClose={onClose}
      title={t("resources.instanceDetail")}
      closeLabel={t("resources.closeDrawer")}
    >
      <p className="help-text">{t("resources.instanceDetailHelp")}</p>
      {query.status === "error" ? (
        <ErrorState message={t("resources.detailUnavailable")} onRetry={query.reload} />
      ) : !instance ? (
        <LoadingState />
      ) : (
        <>
          <dl className="definition-grid">
            <DefinitionItem label={t("resources.instanceName")}>{instance.name}</DefinitionItem>
            <DefinitionItem label={t("resources.instanceId")} mono>
              {instance.id}
            </DefinitionItem>
            <DefinitionItem label={t("resources.col.state")}>
              <StatusBadge label={instance.state} />
            </DefinitionItem>
            <DefinitionItem label={t("resources.col.offering")}>
              {instance.offering_name ?? instance.offering_id ?? "—"}
            </DefinitionItem>
            <DefinitionItem label={t("resources.template")}>
              {instance.template_name ?? instance.template_id ?? "—"}
            </DefinitionItem>
            <DefinitionItem label={t("resources.col.zone")}>
              {instance.zone_name ?? instance.zone_id ?? "—"}
            </DefinitionItem>
            <DefinitionItem label={t("resources.ips")}>
              {instance.ip_addresses.length > 0 ? instance.ip_addresses.join(", ") : "—"}
            </DefinitionItem>
            <DefinitionItem label={t("resources.col.account")}>
              {instance.account ?? instance.account_id ?? "—"}
            </DefinitionItem>
            <DefinitionItem label={t("resources.col.created")}>
              {formatDateTime(instance.created, locale)}
            </DefinitionItem>
          </dl>

          <div className="drawer-section">
            <h3>{t("resources.console")}</h3>
            <p className="help-text">{t("resources.consoleHelp")}</p>
            <p className="help-text">{t("resources.consoleFeatureNote")}</p>
            {consoleError ? <Alert tone="danger">{consoleError}</Alert> : null}
            {config.features.console ? (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void openConsole()}
                disabled={opening}
              >
                {opening ? <Spinner /> : <Icon name="monitor" size={16} />}
                {opening ? t("resources.openingConsole") : t("resources.console")}
              </button>
            ) : (
              <Alert tone="warning">{t("resources.consoleDisabled")}</Alert>
            )}
          </div>
        </>
      )}
    </Drawer>
  );
}

export function ResourcesPage() {
  const { t, locale } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const param = searchParams.get("tab");
  const activeTab: TabId = isTabId(param) ? param : "instances";
  const query = useApiQuery((signal) => api.getResources(activeTab, signal), [activeTab]);
  const [selectedInstance, setSelectedInstance] = useState<InstanceResource | null>(null);

  const tabs = [
    { id: "instances", label: t("resources.tab.instances") },
    { id: "volumes", label: t("resources.tab.volumes") },
    { id: "networks", label: t("resources.tab.networks") },
    { id: "offerings", label: t("resources.tab.offerings") },
    { id: "templates", label: t("resources.tab.templates") },
  ];

  const changeTab = (id: string) => {
    if (!isTabId(id)) return;
    const next = new URLSearchParams(searchParams);
    next.set("tab", id);
    setSearchParams(next, { replace: true });
    setSelectedInstance(null);
  };

  const items = query.data?.items;
  const rowsForTab: Array<unknown> =
    activeTab === "instances"
      ? items?.instances ?? []
      : activeTab === "volumes"
        ? items?.volumes ?? []
        : activeTab === "networks"
          ? items?.networks ?? []
          : activeTab === "offerings"
            ? items?.offerings ?? []
            : items?.templates ?? [];
  const empty = rowsForTab.length === 0;

  return (
    <section className="page" aria-labelledby="resources-title">
      <PageHeader
        title={t("resources.title")}
        description={t("resources.subtitle")}
        actions={
          <button type="button" className="btn btn-secondary" onClick={query.reload}>
            <Icon name="refresh" size={16} />
            {t("app.refresh")}
          </button>
        }
      />

      <Tabs items={tabs} active={activeTab} onChange={changeTab} label={t("resources.tabs.label")} />

      <div className="resources-toolbar">
        {query.data?.refreshed_at ? (
          <span className="part-meta">
            {t("resources.refreshedAt", {
              date: formatDateTime(query.data.refreshed_at, locale),
            })}
          </span>
        ) : null}
      </div>

      <TabPanel id={activeTab} active={activeTab}>
        {query.initialLoading ? (
          <LoadingState />
        ) : query.status === "error" ? (
          <ErrorState message={t("resources.loadError")} onRetry={query.reload} />
        ) : empty ? (
          <EmptyState title={t("resources.empty")} />
        ) : (
          <div className="table-shell">
            {activeTab === "instances" ? (
              <InstancesTable
                rows={items?.instances ?? []}
                onDetails={setSelectedInstance}
              />
            ) : activeTab === "volumes" ? (
              <VolumesTable rows={items?.volumes ?? []} />
            ) : activeTab === "networks" ? (
              <NetworksTable rows={items?.networks ?? []} />
            ) : activeTab === "offerings" ? (
              <OfferingsTable rows={items?.offerings ?? []} />
            ) : (
              <TemplatesTable rows={items?.templates ?? []} />
            )}
          </div>
        )}
      </TabPanel>

      {selectedInstance ? (
        <InstanceDrawer
          instanceId={selectedInstance.id}
          fallback={selectedInstance}
          onClose={() => setSelectedInstance(null)}
        />
      ) : null}
    </section>
  );
}
