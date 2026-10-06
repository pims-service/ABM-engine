export function PageHeading({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <section>
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-1 text-sm text-gray-500">{description}</p>
    </section>
  );
}
