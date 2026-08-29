import React, { useState } from 'react';
import { CustomerSalesReport } from './CustomerSalesReport';
import { ContractPerformanceReport } from './ContractPerformanceReport';
import { WeeklyReportsTab } from './WeeklyReportsTab';
import { useLanguage } from '../lib/language';
type ReportTab = 'customer_sales' | 'contract_performance' | 'weekly_reports';

export const ReportView: React.FC = () => {
  const { language } = useLanguage();
  const text = language === 'en'
    ? {
      customerSales: 'Customer Sales',
      contractPerformance: 'Contract Performance',
      weeklyReports: 'Weekly Reports',
    }
    : {
      customerSales: '客户销售情况',
      contractPerformance: '销售合同执行情况',
      weeklyReports: '周报',
    };

  const [activeTab, setActiveTab] = useState<ReportTab>('customer_sales');

  const tabClass = (tab: ReportTab) =>
    `px-3 py-1 rounded text-xs font-medium transition-colors ${activeTab === tab
      ? 'bg-blue-100 text-blue-700 border border-blue-200'
      : 'bg-slate-100 text-slate-600 border border-slate-200 hover:bg-slate-200'
    }`;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="h-9 bg-white border-b border-slate-200 flex items-center px-4 gap-3 flex-shrink-0">
        <button className={tabClass('customer_sales')} onClick={() => setActiveTab('customer_sales')}>
          {text.customerSales}
        </button>
        <button className={tabClass('contract_performance')} onClick={() => setActiveTab('contract_performance')}>
          {text.contractPerformance}
        </button>
        <button className={tabClass('weekly_reports')} onClick={() => setActiveTab('weekly_reports')}>
          {text.weeklyReports}
        </button>
      </div>

      {activeTab === 'customer_sales' ? (
        <CustomerSalesReport />
      ) : activeTab === 'contract_performance' ? (
        <ContractPerformanceReport />
      ) : (
        <WeeklyReportsTab />
      )}
    </div>
  );
};
