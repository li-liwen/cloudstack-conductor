import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import { useI18n } from "../i18n/I18nProvider";

export function NotFoundPage() {
  const { t } = useI18n();
  return (
    <section className="page not-found" aria-labelledby="not-found-title">
      <p className="not-found-code" aria-hidden="true">
        404
      </p>
      <h1 id="not-found-title">{t("app.pageNotFoundTitle")}</h1>
      <p>{t("app.pageNotFoundBody")}</p>
      <Link className="btn btn-primary" to="/">
        <Icon name="arrowLeft" size={16} />
        {t("app.backHome")}
      </Link>
    </section>
  );
}
