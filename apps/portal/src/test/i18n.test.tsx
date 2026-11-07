import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import { LOCALE_STORAGE_KEY } from "../i18n/locale";

function LocaleProbe() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span data-testid="title">{t("plans.title")}</span>
      <span data-testid="interpolated">{t("workspace.resourceCount", { count: 3 })}</span>
      <button type="button" onClick={() => setLocale("en")}>
        English
      </button>
      <button type="button" onClick={() => setLocale("zh-CN")}>
        中文
      </button>
    </div>
  );
}

describe("i18n", () => {
  it("defaults to zh-CN and persists an English switch", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <LocaleProbe />
      </I18nProvider>,
    );

    expect(screen.getByTestId("locale")).toHaveTextContent("zh-CN");
    expect(screen.getByTestId("title")).toHaveTextContent("计划");
    expect(screen.getByTestId("interpolated")).toHaveTextContent("共 3 项");

    await user.click(screen.getByRole("button", { name: "English" }));

    expect(screen.getByTestId("locale")).toHaveTextContent("en");
    expect(screen.getByTestId("title")).toHaveTextContent("Plans");
    expect(screen.getByTestId("interpolated")).toHaveTextContent("3 items");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("uses the persisted locale and the configured fallback", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "en");
    const { unmount } = render(
      <I18nProvider defaultLocale="zh-CN">
        <LocaleProbe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("title")).toHaveTextContent("Plans");
    unmount();

    window.localStorage.clear();
    render(
      <I18nProvider defaultLocale="en">
        <LocaleProbe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("title")).toHaveTextContent("Plans");
  });
});
