import { useConfig } from "../config/ConfigProvider";

export function Brand({ compact = false }: { compact?: boolean }) {
  const { config } = useConfig();
  const branding = config.branding;
  const showCompany = !compact && branding.company_name !== branding.product_name;

  return (
    <span className="brand">
      <img
        className="brand-logo"
        src={branding.logo_url}
        alt=""
        width={36}
        height={36}
        onError={(event) => {
          const image = event.currentTarget;
          if (image.dataset.fallback !== "true") {
            image.dataset.fallback = "true";
            image.src = "/branding/logo.svg";
          }
        }}
      />
      <span className="brand-text">
        <span className="brand-name">{branding.product_name}</span>
        {showCompany ? <span className="brand-company">{branding.company_name}</span> : null}
      </span>
    </span>
  );
}
