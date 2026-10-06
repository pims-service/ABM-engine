import Link from "next/link";

export default function NotFound() {
  return (
    <section>
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-1 text-sm text-gray-500">
        The page you are looking for does not exist.
      </p>
      <Link
        href="/dashboard"
        className="mt-4 inline-block rounded-md bg-gray-900 px-3 py-2 text-sm text-white"
      >
        Back to dashboard
      </Link>
    </section>
  );
}
