// No-auth mode: Stack Auth handler is disabled
export default async function Handler() {
  return (
    <div style={{ padding: '20px', textAlign: 'center' }}>
      <h1>Local Auth Mode</h1>
      <p>Stack Auth handler is disabled when using local authentication.</p>
    </div>
  );
}
