// Arrow functions and function expressions bound to const.
const double = (x) => x * 2;

const addAll = (...xs) => xs.reduce((a, b) => a + b, 0);

const makeGreeter = function (prefix) {
  return (name) => `${prefix} ${name}`;
};

let mutableCounter = 0;
