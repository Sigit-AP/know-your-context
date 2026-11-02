// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

abstract contract Animal {
    function speak() public pure virtual returns (string memory);

    function describe() public pure returns (string memory) {
        return speak();
    }
}

contract Dog is Animal {
    function speak() public pure override returns (string memory) {
        return "woof";
    }
}

contract Puppy is Dog {
}
