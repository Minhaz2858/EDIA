import React, { useState } from 'react';
import { useLanguage } from '../lib/language';

interface ForcePasswordChangeScreenProps {
    username: string;
    onSubmit: (currentPassword: string, newPassword: string) => Promise<void>;
    isLoading?: boolean;
    error?: string | null;
}

export const ForcePasswordChangeScreen: React.FC<ForcePasswordChangeScreenProps> = ({
    username,
    onSubmit,
    isLoading = false,
    error = null,
}) => {
    const { language } = useLanguage();
    const text = language === 'en'
        ? {
            allFieldsRequired: 'All fields are required.',
            passwordsMismatch: 'New password and confirmation do not match.',
            title: 'Password Update Required',
            heading: `Change password for ${username}`,
            description: 'This account was created or reset by an administrator. Choose a new password before using the system.',
            currentPassword: 'Current password',
            newPassword: 'New password',
            confirmNewPassword: 'Confirm new password',
            updating: 'Updating...',
            updatePassword: 'Update Password',
        }
        : {
            allFieldsRequired: '所有字段均为必填项。',
            passwordsMismatch: '新密码与确认密码不一致。',
            title: '需要更新密码',
            heading: `为 ${username} 修改密码`,
            description: '该账户由管理员创建或重置。请在继续使用系统前设置新的密码。',
            currentPassword: '当前密码',
            newPassword: '新密码',
            confirmNewPassword: '确认新密码',
            updating: '更新中...',
            updatePassword: '更新密码',
        };
    const [currentPassword, setCurrentPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [localError, setLocalError] = useState<string | null>(null);

    const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        setLocalError(null);

        if (!currentPassword || !newPassword || !confirmPassword) {
            setLocalError(text.allFieldsRequired);
            return;
        }

        if (newPassword !== confirmPassword) {
            setLocalError(text.passwordsMismatch);
            return;
        }

        await onSubmit(currentPassword, newPassword);
    };

    return (
        <div className="login-screen-root min-h-screen flex items-center justify-center px-4">
            <div className="login-screen-card w-full max-w-lg rounded-3xl border p-8">
                <div className="mb-6">
                    <p className="text-xs font-semibold uppercase tracking-[0.24em] text-blue-600">{text.title}</p>
                    <h1 className="mt-2 text-2xl font-semibold text-slate-900">{text.heading}</h1>
                    <p className="mt-2 text-sm text-slate-600">
                        {text.description}
                    </p>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label className="mb-1.5 block text-sm font-medium text-slate-700">{text.currentPassword}</label>
                        <input
                            type="password"
                            value={currentPassword}
                            onChange={(e) => setCurrentPassword(e.target.value)}
                            autoComplete="current-password"
                            className="login-screen-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-slate-900 outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
                            disabled={isLoading}
                        />
                    </div>

                    <div>
                        <label className="mb-1.5 block text-sm font-medium text-slate-700">{text.newPassword}</label>
                        <input
                            type="password"
                            value={newPassword}
                            onChange={(e) => setNewPassword(e.target.value)}
                            autoComplete="new-password"
                            className="login-screen-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-slate-900 outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
                            disabled={isLoading}
                        />
                    </div>

                    <div>
                        <label className="mb-1.5 block text-sm font-medium text-slate-700">{text.confirmNewPassword}</label>
                        <input
                            type="password"
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                            autoComplete="new-password"
                            className="login-screen-input w-full rounded-2xl border border-slate-200 px-4 py-3 text-slate-900 outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
                            disabled={isLoading}
                        />
                    </div>

                    {(localError || error) && (
                        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                            {localError || error}
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={isLoading}
                        className="w-full rounded-2xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"
                    >
                        {isLoading ? text.updating : text.updatePassword}
                    </button>
                </form>
            </div>
        </div>
    );
};