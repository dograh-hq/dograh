export default function MaintenancePage() {
  const message =
    process.env.MAINTENANCE_MESSAGE ||
    'We are currently performing scheduled maintenance. Please check back soon.';

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <div className="mb-6 text-6xl">🔧</div>
        <h1 className="mb-4 text-2xl font-bold text-foreground">
          Under Maintenance
        </h1>
        <p className="text-muted-foreground">{message}</p>
      </div>
    </div>
  );
}
