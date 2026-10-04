# Mode: Piecemeal Growth

Piecemeal growth designs from forces that exist now: executable requirements, current workflows, and failures that have actually occurred. Like a desire path, the shape follows observed traffic; it is not paved in anticipation of journeys nobody has taken.

In this mode, keep the implementation as narrow as the present contract. Do not add configurability, concurrency, fallback paths, validation, or abstractions for possible future uses. Make assumptions explicit and let violations fail loudly, so new pressure is visible instead of being absorbed by speculative machinery.

When a new requirement or repeated failure appears, repair the design locally. Generalize only once reality has shown what the generalization must support.

This is not an argument against integrity at real boundaries: protect durable data, external callers, security, and failures with demonstrated likelihood or cost. It is an argument against paying complexity for imagined ones.
