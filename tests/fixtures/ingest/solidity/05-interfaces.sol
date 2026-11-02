// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IShape {
    function area() external view returns (uint256);
    function name() external pure returns (string memory);
}

interface INamed {
    function name() external pure returns (string memory);
}

contract Circle is IShape {
    uint256 public radius;

    constructor(uint256 r) {
        radius = r;
    }

    function area() external view returns (uint256) {
        return 3 * radius * radius;
    }

    function name() external pure returns (string memory) {
        return "circle";
    }
}
