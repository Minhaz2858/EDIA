import type { UserPrefs } from "@/services/api";

export const USER_PREFS_STORAGE_KEY = "edia_user_prefs";

export function loadUserPrefs(languageOverride?: "zh" | "en"): UserPrefs {
  const fallbackLanguage = languageOverride || "zh";
  const fallback: UserPrefs = {
    language: fallbackLanguage,
    delta_basis: "wow",
    default_format: "full_report",
    preferred_products: [],
  };

  if (typeof window === "undefined") return fallback;

  try {
    const raw = window.localStorage.getItem(USER_PREFS_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as UserPrefs;
    return {
      language: languageOverride || parsed.language || fallbackLanguage,
      delta_basis: parsed.delta_basis || "wow",
      default_format: parsed.default_format || "full_report",
      preferred_products: parsed.preferred_products || [],
      chat_mode: parsed.chat_mode,
    };
  } catch {
    return fallback;
  }
}

export function saveUserPrefs(prefs: UserPrefs): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(USER_PREFS_STORAGE_KEY, JSON.stringify(prefs));
}
