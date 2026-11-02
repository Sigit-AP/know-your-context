// References, rvalue refs and move semantics. */
#include <utility>

void Swap(int &a, int &b) {
  int tmp = a;
  a = b;
  b = tmp;
}

class Buffer {
 public:
  Buffer() = default;
  Buffer(Buffer &&other) noexcept = default;
  Buffer &operator=(Buffer &&other) noexcept = default;

  int Size() const { return 0; }
};

Buffer MakeBuffer() { return Buffer(); }
