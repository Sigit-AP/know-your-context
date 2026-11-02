// Standard and local includes.
#include <algorithm>
#include <memory>
#include <vector>
#include "detail/util.hpp"

int count_positive(const std::vector<int> &xs) {
  return (int)std::count_if(xs.begin(), xs.end(), [](int x) { return x > 0; });
}
