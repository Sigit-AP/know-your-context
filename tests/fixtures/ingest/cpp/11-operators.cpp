// Operator overloading and conversions. */
class Money {
 public:
  explicit Money(long cents) : cents_(cents) {}

  Money operator+(const Money &other) const { return Money(cents_ + other.cents_); }

  bool operator==(const Money &other) const { return cents_ == other.cents_; }

  explicit operator long() const { return cents_; }

 private:
  long cents_;
};
