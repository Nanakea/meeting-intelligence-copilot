import React, { useState, useEffect } from "react";
import { getVersion } from '@tauri-apps/api/app';
import Image from 'next/image';


export function About() {
    const [currentVersion, setCurrentVersion] = useState<string>('0.6.1');

    useEffect(() => {
        // Get current version on mount
        getVersion().then(setCurrentVersion).catch(console.error);
    }, []);

    return (
        <div className="p-4 space-y-4 h-[80vh] overflow-y-auto">
            {/* Compact Header */}
            <div className="text-center">
                <div className="mb-3">
                    <Image
                        src="icon_128x128.png"
                        alt="Meeting Intelligence Copilot logo"
                        width={64}
                        height={64}
                        className="mx-auto"
                    />
                </div>
                <h1 className="text-xl font-bold text-gray-900">Meeting Intelligence Copilot</h1>
                <span className="text-sm text-gray-500"> v{currentVersion}</span>
                <p className="text-medium text-gray-600 mt-1">
                    Evidence-backed questions for live JA/EN/KO meetings.
                </p>
                <p className="mt-2 text-xs text-gray-500">
                    Pilot updates are installed manually from approved release packages.
                </p>
            </div>

            {/* Features Grid - Compact */}
            <div className="space-y-3">
                <h2 className="text-base font-semibold text-gray-800">What the copilot does</h2>
                <div className="grid grid-cols-2 gap-2">
                    <div className="bg-gray-50 rounded p-3 hover:bg-gray-100 transition-colors">
                        <h3 className="font-bold text-sm text-gray-900 mb-1">Privacy-first</h3>
                        <p className="text-xs text-gray-600 leading-relaxed">Meeting state and local indexes stay on this Windows account. Remote sources are read-only.</p>
                    </div>
                    <div className="bg-gray-50 rounded p-3 hover:bg-gray-100 transition-colors">
                        <h3 className="font-bold text-sm text-gray-900 mb-1">Question-first</h3>
                        <p className="text-xs text-gray-600 leading-relaxed">Shows one ASK NOW question and at most two follow-ups, backed by transcript evidence.</p>
                    </div>
                    <div className="bg-gray-50 rounded p-3 hover:bg-gray-100 transition-colors">
                        <h3 className="font-bold text-sm text-gray-900 mb-1">Connected evidence</h3>
                        <p className="text-xs text-gray-600 leading-relaxed">Can retrieve approved Notion, Microsoft 365, ERP, repository, and collaboration context.</p>
                    </div>
                    <div className="bg-gray-50 rounded p-3 hover:bg-gray-100 transition-colors">
                        <h3 className="font-bold text-sm text-gray-900 mb-1">Platform-neutral</h3>
                        <p className="text-xs text-gray-600 leading-relaxed">Works with Zoom, Google Meet, and Teams audio captured through the local desktop recorder.</p>
                    </div>
                </div>
            </div>

            <div className="pt-2 border-t border-gray-200 text-center">
                <p className="text-xs text-gray-500">
                    Product integration by Nanakea. Desktop capture foundation derived from the
                    MIT-licensed Meetily project; see the bundled license and notice.
                </p>
            </div>
        </div>

    )
}
