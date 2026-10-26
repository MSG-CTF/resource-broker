export default function AzureWindowsVm({ children }) {
  return (
    <>
      <div className="view-title">
        <div>
          <span className="eyebrow">Azure Windows VM</span>
          <h1>Windows VM</h1>
        </div>
      </div>
      {children || <p>Azure 계정을 동기화하면 Windows VM 목록을 확인할 수 있습니다.</p>}
    </>
  );
}
