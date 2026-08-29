import React, { useState } from "react";
import { useLanguage } from "../lib/language";

interface LoginScreenProps {
  onLogin: (username: string, password: string) => Promise<void>;
  error?: string | null;
  isLoading?: boolean;
}

export const LoginScreen: React.FC<LoginScreenProps> = ({
  onLogin,
  error = null,
  isLoading = false,
}) => {
  const { language } = useLanguage();
  const text =
    language === "en"
      ? {
          secureSignIn: "Secure Sign In",
          title: "伊斯科智能分析决策系统",
          userId: "User ID",
          password: "Password",
          enterUserId: "Enter user ID",
          enterPassword: "Enter password",
          signingIn: "Signing in...",
          signIn: "Sign In",
        }
      : {
          secureSignIn: "安全登录",
          title: "伊斯科智能分析决策系统",
          userId: "用户 ID",
          password: "密码",
          enterUserId: "输入用户 ID",
          enterPassword: "输入密码",
          signingIn: "登录中...",
          signIn: "登录",
        };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onLogin(username, password);
  };

  return (
    <div className="login-screen-root min-h-screen flex items-center justify-center px-4">
      <div className="login-screen-card w-full max-w-md rounded-3xl border backdrop-blur-xl p-8">
        <div className="flex items-center gap-3 mb-8">
          <img src="/logo.png" alt="EDIA" className="h-10 object-contain" />
          <div>
            <p className="login-screen-secure-label text-xs font-semibold uppercase tracking-[0.24em] text-emerald-600">
              {text.secureSignIn}
            </p>
            <h1 className="text-2xl font-semibold text-slate-900">
              {text.title}
            </h1>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-slate-700">
              {text.userId}
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              className="login-screen-input w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-slate-900 outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
              placeholder={text.enterUserId}
              disabled={isLoading}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-slate-700">
              {text.password}
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="login-screen-input w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-slate-900 outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
              placeholder={text.enterPassword}
              disabled={isLoading}
            />
          </div>

          {error && (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isLoading || !username.trim() || !password.trim()}
            className="w-full rounded-2xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:bg-slate-300 disabled:text-slate-500"
          >
            {isLoading ? text.signingIn : text.signIn}
          </button>
        </form>
      </div>
    </div>
  );
};
