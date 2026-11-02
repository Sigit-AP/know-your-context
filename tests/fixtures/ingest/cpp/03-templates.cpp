// Function and class templates.
template <typename T>
T identity(T value) {
  return value;
}

template <typename T, int N>
class FixedArray {
 public:
  int Size() const { return N; }
  T &At(int i) { return data_[i]; }

 private:
  T data_[N];
};

template <typename T>
T MaxOf(T a, T b) {
  return a < b ? b : a;
}
