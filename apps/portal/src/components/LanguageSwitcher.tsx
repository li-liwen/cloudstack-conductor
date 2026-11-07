import { useI18n } from "../i18n/I18nProvider";
import { localeLabel, type Locale } from "../i18n/locale";

export function LanguageSwitcher({ id = "locale-switcher" }: { id?: string }) {
  const { locale, setLocale, t, availableLocales } = useI18n();
  return (
    <label className="locale-switch" htmlFor={id}>
      <span className="visually-hidden">{t("app.language")}</span>
      <select
        id={id}
        className="locale-select"
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        aria-label={t("app.language")}
      >
        {availableLocales.map((item) => (
          <option key={item} value={item}>
            {localeLabel(item)}
          </option>
        ))}
      </select>
    </label>
  );
}
