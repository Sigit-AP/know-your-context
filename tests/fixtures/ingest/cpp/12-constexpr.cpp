// constexpr, noexcept and trailing return types. */
constexpr int Square(int x) { return x * x; }

constexpr int kLimit = 64;

auto Divide(int a, int b) noexcept(false) -> double { return (double)a / (double)b; }

static_assert(kLimit > 0, "limit must be positive");
